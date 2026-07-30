from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.data import crypto_4h_provider as base
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


def frozen_grid() -> list[Candle]:
    return hourly_grid(base.EXPECTED_START, base.EXPECTED_HOURLY_BARS)


def test_isolated_gaps_use_previous_close_and_zero_volume() -> None:
    start = base.EXPECTED_START
    missing = {start + timedelta(hours=100), start + timedelta(hours=101)}
    raw = [item for item in frozen_grid() if item.timestamp not in missing]
    completed, synthetic, longest = continuity.apply_bounded_continuity(raw)
    assert synthetic == sorted(missing)
    assert longest == 2
    assert len(completed) == base.EXPECTED_HOURLY_BARS
    previous_close = completed[99].close
    for index in (100, 101):
        candle = completed[index]
        assert candle.open == previous_close
        assert candle.high == previous_close
        assert candle.low == previous_close
        assert candle.close == previous_close
        assert candle.volume == 0.0


def test_more_than_six_missing_hours_is_rejected() -> None:
    start = base.EXPECTED_START
    missing = {start + timedelta(hours=index) for index in range(100, 107)}
    raw = [item for item in frozen_grid() if item.timestamp not in missing]
    with pytest.raises(continuity.BoundedContinuityError, match="exceed"):
        continuity.apply_bounded_continuity(raw)


def test_six_consecutive_missing_hours_is_the_frozen_maximum() -> None:
    start = base.EXPECTED_START
    missing = {start + timedelta(hours=index) for index in range(100, 106)}
    raw = [item for item in frozen_grid() if item.timestamp not in missing]
    completed, synthetic, longest = continuity.apply_bounded_continuity(raw)
    assert len(completed) == base.EXPECTED_HOURLY_BARS
    assert len(synthetic) == 6
    assert longest == 6


def test_missing_boundary_hour_is_rejected() -> None:
    raw = frozen_grid()[1:]
    with pytest.raises(continuity.BoundedContinuityError, match="first or final"):
        continuity.apply_bounded_continuity(raw)


def test_future_candle_cannot_change_synthetic_value() -> None:
    start = base.EXPECTED_START
    missing = start + timedelta(hours=100)
    raw = [item for item in frozen_grid() if item.timestamp != missing]
    first, _, _ = continuity.apply_bounded_continuity(raw)
    future_timestamp = start + timedelta(hours=101)
    altered = []
    for candle in raw:
        if candle.timestamp == future_timestamp:
            candle = Candle(
                timestamp=candle.timestamp,
                open=1_000_000.0,
                high=1_000_001.0,
                low=999_999.0,
                close=1_000_000.5,
                volume=candle.volume,
            )
        altered.append(candle)
    second, _, _ = continuity.apply_bounded_continuity(altered)
    assert second[100] == first[100]

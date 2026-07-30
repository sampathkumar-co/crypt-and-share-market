from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.data.crypto_4h_provider import (
    EXPECTED_END_EXCLUSIVE,
    EXPECTED_FOUR_HOUR_BARS,
    EXPECTED_START,
    FourHourDataError,
    _four_hour_bucket,
    aggregate_hourly_to_four_hour,
    fetch_v14_four_hour_bundle,
)
from tradebot.models import Candle


def candle(timestamp: datetime, price: float, volume: float = 1.0) -> Candle:
    return Candle(timestamp, price, price + 2.0, price - 1.0, price + 1.0, volume)


def test_four_hour_bucket_is_utc_aligned() -> None:
    assert _four_hour_bucket(datetime(2026, 1, 1, 7, 42)) == datetime(2026, 1, 1, 4)
    assert _four_hour_bucket(datetime(2026, 1, 1, 20, 59)) == datetime(2026, 1, 1, 20)


def test_aggregate_hourly_uses_exact_ohlcv_contract() -> None:
    start = datetime(2025, 1, 1)
    hourly = [candle(start + timedelta(hours=i), 100.0 + i, float(i + 1)) for i in range(8)]
    four_hour = aggregate_hourly_to_four_hour(list(reversed(hourly)))
    assert len(four_hour) == 2
    first = four_hour[0]
    assert first.timestamp == start
    assert first.open == 100.0
    assert first.high == 105.0
    assert first.low == 99.0
    assert first.close == 104.0
    assert first.volume == 10.0


def test_incomplete_hourly_bucket_is_rejected() -> None:
    start = datetime(2025, 1, 1)
    hourly = [candle(start + timedelta(hours=i), 100.0 + i) for i in (0, 1, 3, 4, 5, 6, 7)]
    four_hour = aggregate_hourly_to_four_hour(hourly)
    assert [item.timestamp for item in four_hour] == [start + timedelta(hours=4)]


def test_frozen_fetch_interval_cannot_change(tmp_path) -> None:
    with pytest.raises(FourHourDataError, match="interval is frozen"):
        fetch_v14_four_hour_bundle(tmp_path, EXPECTED_START + timedelta(hours=4), EXPECTED_END_EXCLUSIVE)


def test_frozen_bar_count_is_5400() -> None:
    assert EXPECTED_FOUR_HOUR_BARS == 5_400
    assert EXPECTED_END_EXCLUSIVE - EXPECTED_START == timedelta(hours=5_400 * 4)

from __future__ import annotations

from datetime import datetime, timedelta

from tradebot.backtest.crypto_multisource_holdout import (
    EXPECTED_EMBARGO_START,
    EXPECTED_HOLDOUT_END,
    EXPECTED_HOLDOUT_START,
    EXPECTED_TEST_END,
    MultiSourceHoldoutConfig,
)
from tradebot.backtest.crypto_multisource_holdout_reporting_hotfix import (
    _report_boundaries,
)
from tradebot.models import Candle


def test_reporting_hotfix_accepts_three_value_period_bounds() -> None:
    end = datetime(2026, 7, 30)
    start = end - timedelta(days=449)
    candles: list[Candle] = []
    for index in range(450):
        timestamp = start + timedelta(days=index)
        price = 100.0 + index
        candles.append(
            Candle(
                timestamp=timestamp,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.5,
                volume=1_000_000.0,
            )
        )

    test_start, test_end, embargo_start, embargo_end = _report_boundaries(
        {"LTCUSDT": candles}, MultiSourceHoldoutConfig()
    )

    assert test_start.date() == EXPECTED_HOLDOUT_START
    assert test_end.date() == EXPECTED_TEST_END
    assert embargo_start.date() == EXPECTED_EMBARGO_START
    assert embargo_end.date() == EXPECTED_HOLDOUT_END

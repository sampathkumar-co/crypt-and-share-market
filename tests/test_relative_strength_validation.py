from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.backtest.relative_strength_validation import (
    REQUIRED_SYMBOLS,
    RelativeStrengthValidationConfig,
    compounded_return,
    split_validation_histories,
)
from tradebot.backtest.research_gate import independent_train_test_windows
from tradebot.models import Candle


def candles(count: int, offset: int = 0) -> list[Candle]:
    start = datetime(2023, 1, 1) + timedelta(days=offset)
    return [
        Candle(
            timestamp=start + timedelta(days=index),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.5 + index,
            volume=1000.0 + index,
        )
        for index in range(count)
    ]


def test_validation_split_excludes_latest_discovery_window():
    config = RelativeStrengthValidationConfig()
    histories = {symbol: candles(1000) for symbol in REQUIRED_SYMBOLS}

    full, validation = split_validation_histories(histories, config)

    for symbol in REQUIRED_SYMBOLS:
        assert len(full[symbol]) == 1000
        assert len(validation[symbol]) == 635
        assert validation[symbol][-1].timestamp < full[symbol][-365].timestamp
        windows = independent_train_test_windows(validation[symbol], 180, 60)
        assert len(windows) == 7
        unseen_timestamps = [
            candle.timestamp
            for _, unseen in windows
            for candle in unseen
        ]
        assert len(unseen_timestamps) == len(set(unseen_timestamps))


def test_split_uses_latest_1000_bars_before_holdout():
    config = RelativeStrengthValidationConfig()
    histories = {symbol: candles(1100) for symbol in REQUIRED_SYMBOLS}

    full, validation = split_validation_histories(histories, config)

    assert full["BTCUSDT"][0].timestamp == histories["BTCUSDT"][100].timestamp
    assert validation["BTCUSDT"][-1].timestamp == full["BTCUSDT"][-366].timestamp


def test_compounded_return_is_chronological_product():
    assert compounded_return([0.10, -0.05, 0.02]) == pytest.approx(
        1.10 * 0.95 * 1.02 - 1.0
    )


def test_validation_requires_complete_history_for_every_asset():
    config = RelativeStrengthValidationConfig()
    histories = {symbol: candles(1000) for symbol in REQUIRED_SYMBOLS}
    histories["ADAUSDT"] = candles(999)

    with pytest.raises(ValueError, match="ADAUSDT has 999 candles"):
        split_validation_histories(histories, config)


def test_frozen_validation_split_cannot_be_redefined():
    with pytest.raises(ValueError, match="1000/365"):
        RelativeStrengthValidationConfig(history_bars=900)


def test_cash_reserve_and_exposure_cannot_conflict():
    with pytest.raises(ValueError, match="cash_reserve"):
        RelativeStrengthValidationConfig(
            max_total_exposure=0.80,
            min_cash_reserve=0.40,
        )

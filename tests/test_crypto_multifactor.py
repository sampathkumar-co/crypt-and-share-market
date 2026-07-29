from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.backtest.crypto_multifactor import (
    FULL_FACTOR_WEIGHTS,
    PRICE_FACTOR_WEIGHTS,
    VARIANTS,
    CryptoMultiFactorConfig,
    _capped_weights,
    _feature_row,
    _period_bounds,
    _simulate_period,
    _target_weights,
)
from tradebot.models import Candle


def history(
    bars: int,
    daily_return: float,
    *,
    start_price: float = 100.0,
    volume: float = 1_000_000.0,
    volume_growth: float = 0.0,
) -> list[Candle]:
    candles: list[Candle] = []
    price = start_price
    start = datetime(2020, 1, 1)
    for index in range(bars):
        opened = price
        closed = opened * (1.0 + daily_return)
        high = max(opened, closed) * 1.005
        low = min(opened, closed) * 0.995
        candles.append(
            Candle(
                timestamp=start + timedelta(days=index),
                open=opened,
                high=high,
                low=low,
                close=closed,
                volume=volume * (1.0 + volume_growth * index),
            )
        )
        price = closed
    return candles


def test_frozen_split_and_factor_weights_are_complete():
    config = CryptoMultiFactorConfig()
    assert config.total_periods == 12
    assert _period_bounds(1, config) == (240, 359, "early")
    assert _period_bounds(6, config) == (840, 959, "early")
    assert _period_bounds(7, config) == (990, 1109, "late")
    assert _period_bounds(12, config) == (1590, 1709, "late")
    assert sum(FULL_FACTOR_WEIGHTS.values()) == pytest.approx(1.0)
    assert sum(PRICE_FACTOR_WEIGHTS.values()) == pytest.approx(1.0)


def test_capped_weights_preserve_exposure_and_caps():
    weights = _capped_weights({"A": 10.0, "B": 2.0, "C": 1.0}, 0.75, 0.35)
    assert sum(weights.values()) == pytest.approx(0.75)
    assert max(weights.values()) <= 0.35 + 1e-12
    assert all(value >= 0 for value in weights.values())


def test_bear_market_abstains_to_cash():
    prior = {
        symbol: history(260, -0.003 - index * 0.0002)
        for index, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT"))
    }
    weights, regime = _target_weights(prior, VARIANTS[0], 1.0)
    assert weights == {}
    assert regime in {"bear", "crisis"}


def test_full_factor_row_rewards_confirmed_liquid_trend():
    leader = history(260, 0.003, volume=2_000_000.0, volume_growth=0.002)
    laggard = history(260, -0.0005, volume=100_000.0)
    leader_features = _feature_row(leader, leader)
    laggard_features = _feature_row(laggard, leader)
    assert leader_features["momentum_60"] > laggard_features["momentum_60"]
    assert leader_features["trend_quality"] > laggard_features["trend_quality"]
    assert leader_features["log_liquidity"] > laggard_features["log_liquidity"]
    assert leader_features["ma_distance"] > 0


def test_multifactor_target_is_diversified_and_keeps_cash():
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT")
    returns = (0.0015, 0.0018, 0.0021, 0.0008, 0.0011)
    prior = {
        symbol: history(300, daily_return, volume=1_000_000.0 + index * 200_000.0)
        for index, (symbol, daily_return) in enumerate(zip(symbols, returns))
    }
    weights, regime = _target_weights(prior, VARIANTS[0], 1.0)
    assert regime in {"bull", "neutral"}
    assert 1 <= len(weights) <= VARIANTS[0].top_n
    assert sum(weights.values()) <= 1.0 - VARIANTS[0].min_cash_reserve + 1e-12
    assert max(weights.values()) <= VARIANTS[0].max_asset_weight + 1e-12


def test_one_synthetic_period_runs_with_real_cost_accounting():
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT")
    returns = (0.0007, 0.0009, 0.0012, 0.0004, 0.0006)
    histories = {
        symbol: history(1800, daily_return, volume=2_000_000.0 + index * 250_000.0)
        for index, (symbol, daily_return) in enumerate(zip(symbols, returns))
    }
    result = _simulate_period(histories, 1, VARIANTS[0], CryptoMultiFactorConfig())
    assert result.period == 1
    assert result.phase == "early"
    assert result.test_start < result.test_end
    assert result.transactions >= 0
    assert result.turnover >= 0
    assert result.total_fees >= 0
    assert result.total_slippage >= 0
    assert result.total_tax >= 0
    assert result.max_drawdown >= 0
    assert set(result.selected_symbols).issubset(set(symbols))

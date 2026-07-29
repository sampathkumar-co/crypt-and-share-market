from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.backtest.cross_sectional_trend import (
    CrossSectionalTrendConfig,
    VARIANTS,
    _capped_inverse_volatility_weights,
    _simulate_period,
    _target_weights,
)
from tradebot.models import Candle


def candles(multiplier: float, *, falling: bool = False, count: int = 800) -> list[Candle]:
    start = datetime(2020, 1, 1)
    values: list[Candle] = []
    price = 100.0
    for index in range(count):
        direction = -1.0 if falling else 1.0
        daily = direction * multiplier * (1.0 + (index % 7) * 0.02)
        open_price = price
        close = max(1.0, price * (1.0 + daily))
        high = max(open_price, close) * 1.003
        low = min(open_price, close) * 0.997
        values.append(
            Candle(
                timestamp=start + timedelta(days=index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=1_000_000.0 + index,
            )
        )
        price = close
    return values


def test_frozen_validation_split_has_ten_periods_and_embargo():
    config = CrossSectionalTrendConfig()
    assert config.history_bars == 1800
    assert config.observed_holdout_bars == 1000
    assert config.validation_bars == 800
    assert config.warmup_bars + config.validation_periods * config.test_bars + config.embargo_bars == 800


def test_inverse_volatility_weights_respect_exposure_and_cap():
    weights = _capped_inverse_volatility_weights(
        [("BTC", {"volatility": 0.20}), ("ETH", {"volatility": 0.40})],
        exposure=0.80,
        cap=0.40,
    )
    assert sum(weights.values()) == pytest.approx(0.80)
    assert max(weights.values()) <= 0.40


def test_bear_market_forces_cash():
    config = CrossSectionalTrendConfig()
    histories = {
        "BTCUSDT": candles(0.001, falling=True)[:180],
        "ETHUSDT": candles(0.0012, falling=True)[:180],
        "SOLUSDT": candles(0.0015, falling=True)[:180],
        "XRPUSDT": candles(0.0011, falling=True)[:180],
        "ADAUSDT": candles(0.0013, falling=True)[:180],
    }
    weights, regime_cash = _target_weights(histories, VARIANTS[0], config)
    assert regime_cash
    assert weights == {}


def test_healthy_market_selects_no_more_than_two_primary_assets():
    config = CrossSectionalTrendConfig()
    histories = {
        "BTCUSDT": candles(0.0015)[:180],
        "ETHUSDT": candles(0.0013)[:180],
        "SOLUSDT": candles(0.0011)[:180],
        "XRPUSDT": candles(0.0009)[:180],
        "ADAUSDT": candles(0.0008)[:180],
    }
    weights, regime_cash = _target_weights(histories, VARIANTS[0], config)
    assert not regime_cash
    assert 1 <= len(weights) <= 2
    assert sum(weights.values()) <= 0.80 + 1e-9
    assert max(weights.values()) <= 0.40 + 1e-9


def test_shared_cash_period_runs_with_real_costs_and_no_leverage():
    config = CrossSectionalTrendConfig()
    histories = {
        "BTCUSDT": candles(0.0015),
        "ETHUSDT": candles(0.0013),
        "SOLUSDT": candles(0.0011),
        "XRPUSDT": candles(0.0009),
        "ADAUSDT": candles(0.0008),
    }
    result = _simulate_period(histories, 1, VARIANTS[0], config)
    assert result.active
    assert result.transactions > 0
    assert result.turnover > 0
    assert result.total_fees > 0
    assert result.total_slippage > 0
    assert result.average_cash_weight >= 0
    assert result.max_drawdown >= 0
    assert result.stressed_return <= result.net_return

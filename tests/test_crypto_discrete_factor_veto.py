from __future__ import annotations

import math
from datetime import datetime, timedelta

from tradebot.backtest import crypto_multifactor as base
from tradebot.backtest.crypto_discrete_factor_veto import (
    REQUIRED_SYMBOLS,
    VARIANTS,
    DiscreteFactorVetoConfig,
    _aligned_histories,
    _scale_raw_weights,
    _target_weights,
    _variant_result,
)
from tradebot.data.crypto_provider import save_candles_csv
from tradebot.models import Candle


def _history(
    bars: int,
    daily_return: float,
    *,
    phase: float = 0.0,
    start_price: float = 100.0,
) -> list[Candle]:
    candles: list[Candle] = []
    price = start_price
    start = datetime(2020, 1, 1)
    for index in range(bars):
        modulation = 0.0007 * math.sin(index / 11.0 + phase)
        opening = price
        close = opening * (1.0 + daily_return + modulation)
        candles.append(
            Candle(
                timestamp=start + timedelta(days=index),
                open=opening,
                high=max(opening, close) * 1.01,
                low=min(opening, close) * 0.99,
                close=close,
                volume=1_000_000.0 * (1.0 + 0.05 * math.sin(index / 17.0 + phase)),
            )
        )
        price = close
    return candles


def _histories(bars: int = 1800) -> dict[str, list[Candle]]:
    return {
        symbol: _history(
            bars,
            0.0007 + index * 0.00012,
            phase=float(index),
            start_price=100.0 + index * 5.0,
        )
        for index, symbol in enumerate(REQUIRED_SYMBOLS)
    }


def test_frozen_split_and_variants() -> None:
    config = DiscreteFactorVetoConfig()
    assert config.discovery_bars == 1800
    assert config.holdout_bars == 250
    assert config.total_periods == 12
    assert [variant.name for variant in VARIANTS] == [
        "primary_discrete_veto",
        "conservative_discrete_veto",
        "diversified_discrete_veto",
        "continuous_factor_risk",
        "continuous_risk_only",
        "raw_simple_trend",
    ]


def test_scale_raw_weights_never_increases_exposure() -> None:
    raw = {"A": 0.40, "B": 0.40}
    assert _scale_raw_weights(raw, 0.80) == raw
    scaled = _scale_raw_weights(raw, 0.55)
    assert abs(sum(scaled.values()) - 0.55) < 1e-12
    assert all(scaled[symbol] <= raw[symbol] for symbol in raw)


def test_bear_market_stays_in_cash() -> None:
    prior = {
        symbol: _history(240, -0.001, phase=float(index))
        for index, symbol in enumerate(REQUIRED_SYMBOLS)
    }
    weights, regime = _target_weights(prior, VARIANTS[0], 1.0)
    assert weights == {}
    assert regime == "cash"


def test_discrete_veto_never_exceeds_raw_target() -> None:
    prior = _histories(240)
    veto_weights, _ = _target_weights(prior, VARIANTS[0], 1.0)
    raw_weights, _ = _target_weights(prior, VARIANTS[-1], 1.0)
    assert sum(veto_weights.values()) <= sum(raw_weights.values()) + 1e-12
    assert all(weight <= 0.40 + 1e-12 for weight in veto_weights.values())


def test_raw_arm_exactly_reproduces_simple_trend() -> None:
    histories = _histories()
    config = DiscreteFactorVetoConfig()
    raw = _variant_result(histories, VARIANTS[-1], config)
    reference = base._variant_result(histories, base.VARIANTS[-1], base.CryptoMultiFactorConfig())
    for left, right in zip(raw.periods, reference.periods):
        assert abs(left.net_return - right.net_return) < 1e-12
        assert abs(left.max_drawdown - right.max_drawdown) < 1e-12
        assert left.transactions == right.transactions


def test_alignment_reserves_exact_holdout(tmp_path) -> None:
    for index, symbol in enumerate(REQUIRED_SYMBOLS):
        save_candles_csv(
            symbol,
            _history(2050, 0.0005 + index * 0.00005, phase=float(index)),
            tmp_path,
        )
    full, discovery, holdout = _aligned_histories(tmp_path, DiscreteFactorVetoConfig())
    assert all(len(candles) == 2050 for candles in full.values())
    assert all(len(candles) == 1800 for candles in discovery.values())
    assert all(len(candles) == 250 for candles in holdout.values())
    assert next(iter(discovery.values()))[-1].timestamp < next(iter(holdout.values()))[0].timestamp

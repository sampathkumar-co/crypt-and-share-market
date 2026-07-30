from __future__ import annotations

from datetime import datetime, timedelta

from tradebot.backtest import crypto_multifactor as base
from tradebot.backtest.crypto_factor_risk_overlay import (
    VARIANTS,
    FactorRiskOverlayConfig,
    _correlation_multiplier,
    _factor_exposure_multiplier,
    _overlay_target_weights,
    _variant_result,
)
from tradebot.models import Candle


def _history(
    bars: int,
    daily_return: float,
    *,
    start_price: float = 100.0,
    volume: float = 1_000_000.0,
) -> list[Candle]:
    candles: list[Candle] = []
    price = start_price
    start = datetime(2020, 1, 1)
    for index in range(bars):
        opening = price
        close = opening * (1.0 + daily_return)
        candles.append(
            Candle(
                timestamp=start + timedelta(days=index),
                open=opening,
                high=max(opening, close) * 1.01,
                low=min(opening, close) * 0.99,
                close=close,
                volume=volume * (1.0 + 0.0005 * index),
            )
        )
        price = close
    return candles


def _histories(bars: int = 1800) -> dict[str, list[Candle]]:
    returns = {
        "BTCUSDT": 0.0014,
        "ETHUSDT": 0.0018,
        "SOLUSDT": 0.0022,
        "DOGEUSDT": 0.0010,
        "ADAUSDT": 0.0008,
    }
    return {
        symbol: _history(bars, daily_return, start_price=100.0 + index * 10.0)
        for index, (symbol, daily_return) in enumerate(returns.items())
    }


def test_frozen_split_and_variants() -> None:
    config = FactorRiskOverlayConfig()
    assert config.total_periods == 12
    assert [variant.name for variant in VARIANTS] == [
        "primary_factor_risk",
        "conservative_factor_risk",
        "diversified_factor_risk",
        "risk_only_ablation",
        "raw_simple_trend",
    ]


def test_factor_and_correlation_multipliers_are_bounded() -> None:
    assert _factor_exposure_multiplier(-1.0) == 0.55
    assert _factor_exposure_multiplier(0.0) == 0.55
    assert _factor_exposure_multiplier(1.0) == 1.0
    assert _factor_exposure_multiplier(2.0) == 1.0
    assert _correlation_multiplier(0.50) == 1.0
    assert _correlation_multiplier(0.86) == 0.80
    assert _correlation_multiplier(0.95) == 0.65


def test_unhealthy_trend_stays_in_cash() -> None:
    prior = {symbol: _history(240, -0.001) for symbol in base.REQUIRED_SYMBOLS}
    weights, regime = _overlay_target_weights(prior, VARIANTS[0], 1.0)
    assert weights == {}
    assert regime == "cash"


def test_overlay_weights_preserve_cash_and_caps() -> None:
    prior = _histories(240)
    weights, regime = _overlay_target_weights(prior, VARIANTS[0], 1.0)
    assert regime == "factor_risk"
    assert 0 < sum(weights.values()) <= 0.75 + 1e-12
    assert all(weight <= 0.40 + 1e-12 for weight in weights.values())


def test_raw_arm_exactly_reproduces_v10_simple_trend() -> None:
    histories = _histories()
    overlay = _variant_result(histories, VARIANTS[-1], FactorRiskOverlayConfig())
    reference = base._variant_result(histories, base.VARIANTS[-1], base.CryptoMultiFactorConfig())
    assert len(overlay.periods) == len(reference.periods)
    for left, right in zip(overlay.periods, reference.periods):
        assert abs(left.net_return - right.net_return) < 1e-12
        assert abs(left.max_drawdown - right.max_drawdown) < 1e-12
        assert left.transactions == right.transactions


def test_risk_overlay_never_exceeds_raw_exposure() -> None:
    prior = _histories(240)
    factor_weights, _ = _overlay_target_weights(prior, VARIANTS[0], 1.0)
    raw_weights, _ = _overlay_target_weights(prior, VARIANTS[-1], 1.0)
    assert sum(factor_weights.values()) <= sum(raw_weights.values()) + 1e-12

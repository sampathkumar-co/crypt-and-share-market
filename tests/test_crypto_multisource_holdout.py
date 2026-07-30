from __future__ import annotations

import math

import pytest

from datetime import date, datetime, timedelta

from tradebot.backtest import crypto_multifactor as base
from tradebot.backtest.crypto_multisource_holdout import (
    VARIANTS,
    ExternalFactorStore,
    MultiSourceHoldoutConfig,
    _funding_supportive,
    _macro_supportive,
    _onchain_supportive,
    _scale_weights,
    _stablecoin_supportive,
    _support_scale,
    _target_weights,
)
from tradebot.models import Candle


def _series(start: date, days: int, start_value: float, daily_growth: float = 0.0) -> dict[date, float]:
    return {
        start + timedelta(days=index): start_value * (1.0 + daily_growth) ** index
        for index in range(days)
    }


def _history(bars: int, daily_return: float, phase: float = 0.0) -> list[Candle]:
    start = datetime(2024, 1, 1)
    price = 100.0
    candles: list[Candle] = []
    for index in range(bars):
        opening = price
        close = opening * (1.0 + daily_return + 0.0002 * math.sin(index / 9 + phase))
        candles.append(
            Candle(
                timestamp=start + timedelta(days=index),
                open=opening,
                high=max(opening, close) * 1.01,
                low=min(opening, close) * 0.99,
                close=close,
                volume=1_000_000 * (1.0 + 0.01 * math.sin(index / 13 + phase)),
            )
        )
        price = close
    return candles


def _store() -> ExternalFactorStore:
    start = date(2024, 1, 1)
    stable = {
        "usdt": _series(start, 500, 100.0, 0.001),
        "usdc": _series(start, 500, 50.0, 0.001),
    }
    symbols = ("LTCUSDT", "BCHUSDT", "LINKUSDT")
    onchain = {
        symbol: {
            "AdrActCnt": _series(start, 500, 1000.0 + index, 0.001),
            "TxCnt": _series(start, 500, 2000.0 + index, 0.001),
        }
        for index, symbol in enumerate(symbols)
    }
    funding = {symbol: _series(start, 500, 0.0001, 0.0) for symbol in symbols}
    macro = {
        "VIXCLS": _series(start, 500, 20.0, 0.0),
        "DTWEXBGS": _series(start, 500, 100.0, 0.0),
        "DGS10": _series(start, 500, 4.0, 0.0),
    }
    return ExternalFactorStore(stable, onchain, funding, macro, {}, "fingerprint")


def test_frozen_holdout_split_and_variants() -> None:
    config = MultiSourceHoldoutConfig()
    assert config.test_periods * config.test_bars == 180
    assert config.embargo_bars == 70
    assert [variant.name for variant in VARIANTS] == [
        "primary_multisource",
        "without_stablecoin",
        "without_onchain",
        "without_derivatives",
        "without_macro",
        "raw_simple_trend",
    ]


def test_support_scale_matches_frozen_mapping() -> None:
    assert _support_scale(0, 4) == 0.0
    assert _support_scale(1, 4) == 0.0
    assert _support_scale(2, 4) == 0.50
    assert _support_scale(3, 4) == 0.75
    assert _support_scale(4, 4) == 1.0
    assert _support_scale(2, 3) == 0.50
    assert _support_scale(3, 3) == 1.0


def test_independent_factor_families_support_healthy_state() -> None:
    store = _store()
    as_of = date(2025, 2, 28)
    selected = ["LTCUSDT", "BCHUSDT"]
    assert _stablecoin_supportive(store, as_of)
    assert _onchain_supportive(store, selected, as_of)
    assert _funding_supportive(store, selected, as_of)
    assert _macro_supportive(store, as_of)


def test_future_observations_are_not_used() -> None:
    store = _store()
    as_of = date(2024, 5, 1)
    # Poison only future stablecoin observations; the as-of decision must remain unchanged.
    before = _stablecoin_supportive(store, as_of)
    store.stablecoin["usdt"][date(2025, 1, 1)] = -1e30
    store.stablecoin["usdc"][date(2025, 1, 1)] = -1e30
    assert _stablecoin_supportive(store, as_of) == before


def test_external_factors_cannot_create_trend_entry() -> None:
    store = _store()
    histories = {
        symbol: _history(240, -0.001, phase=float(index))
        for index, symbol in enumerate(("LTCUSDT", "BCHUSDT", "LINKUSDT"))
    }
    weights, regime = _target_weights(histories, VARIANTS[0], 1.0, store)
    assert weights == {}
    assert regime == "trend_cash"


def test_weight_scaling_never_increases_raw_target() -> None:
    raw = {"LTCUSDT": 0.4, "BCHUSDT": 0.4}
    assert sum(_scale_weights(raw, 0.75).values()) == pytest.approx(0.6)
    assert _scale_weights(raw, 2.0) == raw

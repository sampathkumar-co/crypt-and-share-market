from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from tradebot.backtest import crypto_multiregime_4h as engine
from tradebot.models import Candle


def candles_from_closes(values: list[float], start: datetime | None = None) -> list[Candle]:
    start = start or datetime(2024, 1, 1)
    output = []
    for index, close in enumerate(values):
        output.append(
            Candle(
                timestamp=start + timedelta(hours=4 * index),
                open=close - 0.1,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1000.0 + index,
            )
        )
    return output


def neutral_store(symbols: tuple[str, ...] = engine.REQUIRED_SYMBOLS) -> engine.ExternalStore:
    daily = {date(2023, 1, 1) + timedelta(days=index): 100.0 + index for index in range(1500)}
    funding = {
        symbol: {datetime(2023, 1, 1) + timedelta(hours=4 * index): 0.0 for index in range(3000)}
        for symbol in symbols
    }
    return engine.ExternalStore(
        stablecoin={"usdt": daily, "usdc": daily},
        funding=funding,
        macro={"VIXCLS": daily, "DTWEXBGS": daily, "DGS10": daily},
        manifest={"files": []},
        manifest_fingerprint="test",
    )


def test_frozen_split_and_period_boundaries() -> None:
    config = engine.MultiRegimeConfig()
    assert config.discovery_end_exclusive == 3_480
    assert config.embargo_start == 3_480
    assert config.holdout_start == 3_714
    assert engine._period_bounds(1, config, "discovery") == (600, 1_079)
    assert engine._period_bounds(6, config, "discovery") == (3_000, 3_479)
    assert engine._period_bounds(1, config, "holdout") == (3_714, 3_953)
    assert engine._period_bounds(3, config, "holdout") == (4_194, 4_433)


def test_invalid_split_fails_closed() -> None:
    with pytest.raises(ValueError, match="4,434"):
        engine.MultiRegimeConfig(holdout_bars=719)


def test_range_candidate_uses_completed_bounce_confirmation() -> None:
    values = [100.0 + (1.0 if index % 2 else -1.0) for index in range(178)]
    values.extend([80.0, 80.25])
    candidate = engine._range_candidate("AVAXUSDT", candles_from_closes(values))
    assert candidate is not None
    assert candidate.sleeve == "range"


def test_signal_priority_is_funding_then_trend_then_range(monkeypatch) -> None:
    prior = {"AVAXUSDT": candles_from_closes([100.0 + index * 0.1 for index in range(200)])}
    store = neutral_store(("AVAXUSDT",))
    monkeypatch.setattr(engine, "_funding_candidate", lambda symbol, candles, store: engine.Candidate(symbol, "funding", 1.0, 2.0))
    monkeypatch.setattr(engine, "_trend_candidate", lambda symbol, candles: engine.Candidate(symbol, "trend", 99.0, 2.0))
    monkeypatch.setattr(engine, "_range_candidate", lambda symbol, candles: engine.Candidate(symbol, "range", 999.0, 2.0))
    selected = engine.signal_candidates(prior, store, engine.SLEEVES)
    assert selected["AVAXUSDT"].sleeve == "funding"


def test_target_weights_never_leverage_or_break_asset_cap(monkeypatch) -> None:
    symbols = ["AVAXUSDT", "DOTUSDT", "NEARUSDT"]
    histories = {
        symbol: candles_from_closes([100.0 + index * (0.05 + offset * 0.01) for index in range(200)])
        for offset, symbol in enumerate(symbols)
    }
    store = neutral_store(tuple(symbols))
    monkeypatch.setattr(engine, "_external_risk_multiplier", lambda store, as_of: 1.0)
    weights = engine.target_weights(symbols, histories, store, 1.0, engine.MultiRegimeConfig())
    assert sum(weights.values()) <= 0.75 + 1e-12
    assert all(weight <= 0.25 + 1e-12 for weight in weights.values())
    assert all(weight >= 0 for weight in weights.values())


def test_signal_prefix_is_unchanged_by_future_candles() -> None:
    values = [100.0 + index * 0.2 for index in range(195)] + [137.0, 136.0, 135.5, 136.5, 139.0]
    history = candles_from_closes(values)
    prefix = history[:]
    left = engine._trend_candidate("AVAXUSDT", prefix)
    future = prefix + candles_from_closes([1_000.0, 1.0], start=prefix[-1].timestamp + timedelta(hours=4))
    right = engine._trend_candidate("AVAXUSDT", future[: len(prefix)])
    assert left == right


def test_holdout_is_locked_without_discovery_pass(tmp_path) -> None:
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps({"accepted": False, "eligible_for_holdout": False}), encoding="utf-8")
    with pytest.raises(ValueError, match="locked"):
        engine.evaluate_holdout(tmp_path, tmp_path, path)


def test_drawdown_brake_is_frozen() -> None:
    assert engine._drawdown_multiplier(0.049) == 1.0
    assert engine._drawdown_multiplier(0.05) == 0.65
    assert engine._drawdown_multiplier(0.10) == 0.25
    assert engine._drawdown_multiplier(0.15) == 0.0


def test_v141_calendar_boundaries_are_frozen() -> None:
    config = engine.MultiRegimeConfig()
    start = datetime(2023, 11, 15)
    candles = candles_from_closes(
        [100.0] * config.total_bars,
        start=start,
    )
    bounds = engine._date_boundaries({"AVAXUSDT": candles}, config)
    assert bounds == {
        "discovery_test_start": "2024-02-23T00:00:00",
        "discovery_test_end": "2025-06-16T20:00:00",
        "embargo_start": "2025-06-17T00:00:00",
        "embargo_end": "2025-07-25T20:00:00",
        "holdout_start": "2025-07-26T00:00:00",
        "holdout_end": "2025-11-22T20:00:00",
    }

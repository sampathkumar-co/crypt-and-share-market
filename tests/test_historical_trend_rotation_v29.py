from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_trend_rotation_v29 as v29


def feature(**overrides: float) -> v29.Features:
    values = dict(
        return_1=0.01,
        return_3=0.02,
        return_5=0.04,
        return_20=0.10,
        return_60=0.20,
        return_120=0.30,
        return_180=0.40,
        volatility_20=0.02,
        sma_50=90.0,
        sma_80=90.0,
        sma_150=85.0,
        sma_200=80.0,
        close=100.0,
        close_location=0.75,
        volume_ratio=1.2,
        drawdown_20=-0.02,
        trend_score=10.0,
    )
    values.update(overrides)
    return v29.Features(**values)


def model(**overrides: object) -> v29.ModelSpec:
    values = dict(
        sma_length=80,
        breadth_floor=1.0 / 3.0,
        rebalance_days=5,
        top_n=2,
        maximum_exposure=0.30,
        drawdown_brake=0.10,
    )
    values.update(overrides)
    return v29.ModelSpec(**values)


def test_frozen_model_grid_has_exactly_64_unique_models() -> None:
    assert len(v29.MODEL_GRID) == 64
    assert len({item.model_id for item in v29.MODEL_GRID}) == 64
    assert {item.sma_length for item in v29.MODEL_GRID} == {80, 150}
    assert {item.rebalance_days for item in v29.MODEL_GRID} == {5, 10}
    assert {item.top_n for item in v29.MODEL_GRID} == {1, 2}
    assert {item.maximum_exposure for item in v29.MODEL_GRID} == {0.15, 0.30}
    assert {item.drawdown_brake for item in v29.MODEL_GRID} == {0.10, 0.20}


def test_strong_trend_selects_top_two_without_exceeding_30_percent() -> None:
    payload = {
        asset: feature(trend_score=float(index + 1))
        for index, asset in enumerate(v29.ASSETS)
    }
    weights, selected, sleeve, age = v29._target(model(), payload, (), "cash", 0)

    assert selected == ("DOGE", "LINK")
    assert sleeve == "strong_trend"
    assert age == 0
    assert abs(sum(weights.values()) - 0.30) < 1e-12
    assert weights == {"DOGE": 0.15, "LINK": 0.15}


def test_volatility_scaling_reduces_exposure() -> None:
    payload = {asset: feature(volatility_20=0.05) for asset in v29.ASSETS}
    weights, _, sleeve, _ = v29._target(model(), payload, (), "cash", 0)

    assert sleeve == "strong_trend"
    assert abs(sum(weights.values()) - 0.15) < 1e-12


def test_drawdown_brake_halves_risk_scaled_exposure() -> None:
    payload = {asset: feature() for asset in v29.ASSETS}
    payload["BTC"] = feature(drawdown_20=-0.12)
    weights, _, sleeve, _ = v29._target(model(drawdown_brake=0.10), payload, (), "cash", 0)

    assert sleeve == "strong_trend"
    assert abs(sum(weights.values()) - 0.15) < 1e-12


def test_anti_chase_excludes_extreme_short_term_leader() -> None:
    payload = {
        asset: feature(trend_score=float(index + 1))
        for index, asset in enumerate(v29.ASSETS)
    }
    payload["DOGE"] = feature(trend_score=100.0, return_1=0.09)
    weights, selected, _, _ = v29._target(model(top_n=1), payload, (), "cash", 0)

    assert selected == ("LINK",)
    assert set(weights) == {"LINK"}


def test_moderate_regime_allows_only_small_btc_core() -> None:
    payload = {
        asset: feature(return_60=-0.02, return_120=-0.02, close=70.0)
        for asset in v29.ASSETS
    }
    payload["BTC"] = feature(
        return_1=0.01,
        return_60=0.05,
        return_120=0.10,
        close=100.0,
        sma_80=110.0,
        sma_200=90.0,
    )
    payload["ETH"] = feature(return_60=0.01, return_120=-0.01, close=70.0)
    weights, selected, sleeve, age = v29._target(model(), payload, (), "cash", 0)

    assert weights == {"BTC": 0.10}
    assert selected == ("BTC",)
    assert sleeve == "moderate_btc"
    assert age == 0


def test_five_verification_quarters_are_new_and_sequential() -> None:
    assert [window.name for window in v29.VERIFICATION_WINDOWS] == [
        "2025-Q1",
        "2025-Q2",
        "2025-Q3",
        "2025-Q4",
        "2026-Q1",
    ]
    for left, right in zip(v29.VERIFICATION_WINDOWS, v29.VERIFICATION_WINDOWS[1:]):
        assert left.end + timedelta(days=1) == right.start


def test_discovery_contains_thirteen_complete_quarters() -> None:
    windows = v29.discovery_windows()
    assert len(windows) == 13
    assert windows[0].name == "2021-Q4"
    assert windows[-1].name == "2024-Q4"
    assert all(window.end < v29.VERIFICATION_START for window in windows)


def test_mode_and_limits_never_authorize_trading() -> None:
    assert v29.MODE == "HISTORICAL_RISK_SCALED_TREND_ROTATION_ONLY"
    assert max(item.maximum_exposure for item in v29.MODEL_GRID) == 0.30
    assert v29.VERIFICATION_START > v29.DISCOVERY_END

from __future__ import annotations

from tradebot.research import historical_rotation_v28 as v28


def _feature(**overrides: float) -> v28.AssetFeatures:
    values = dict(
        return_1=0.01,
        return_3=0.02,
        return_5=0.03,
        return_20=0.10,
        return_60=0.20,
        return_120=0.30,
        volatility_20=0.02,
        sma_50=90.0,
        sma_80=90.0,
        sma_100=90.0,
        sma_120=90.0,
        close=100.0,
        close_location=0.75,
        volume_ratio=1.5,
        trend_score=10.0,
    )
    values.update(overrides)
    return v28.AssetFeatures(**values)


def test_grid_is_exactly_frozen_32_models() -> None:
    assert len(v28.MODEL_GRID) == 32
    assert len({model.model_id for model in v28.MODEL_GRID}) == 32
    assert {model.sma_length for model in v28.MODEL_GRID} == {80, 120}
    assert {model.breadth_floor for model in v28.MODEL_GRID} == {0.4, 0.6}
    assert {model.rebalance_days for model in v28.MODEL_GRID} == {5, 7}
    assert {model.top_n for model in v28.MODEL_GRID} == {1, 2}
    assert {model.recovery_threshold for model in v28.MODEL_GRID} == {-0.06, -0.10}


def test_trend_rotation_respects_30_percent_exposure() -> None:
    model = v28.ModelSpec(80, 0.4, 5, 2, -0.06)
    features = {asset: _feature(trend_score=10.0 - index) for index, asset in enumerate(v28.ASSETS)}
    weights, sleeve, counter = v28._daily_target(model, features, {}, "cash", 0)
    assert sleeve == "trend"
    assert counter == 0
    assert len(weights) == 2
    assert abs(sum(weights.values()) - 0.30) < 1e-12
    assert all(abs(weight - 0.15) < 1e-12 for weight in weights.values())


def test_recovery_is_available_only_after_trend_is_unhealthy() -> None:
    model = v28.ModelSpec(80, 0.6, 7, 1, -0.06)
    features = {asset: _feature(return_20=-0.1, return_60=-0.1, return_120=-0.1, close=80.0) for asset in v28.ASSETS}
    features["ETH"] = _feature(
        return_1=0.03,
        return_5=-0.08,
        return_20=-0.1,
        return_60=-0.1,
        return_120=-0.2,
        close=80.0,
        close_location=0.8,
        volume_ratio=2.0,
    )
    weights, sleeve, _ = v28._daily_target(model, features, {}, "cash", 0)
    assert sleeve == "recovery"
    assert weights == {"ETH": 0.30}


def test_cash_when_no_sleeve_qualifies() -> None:
    model = v28.ModelSpec(120, 0.6, 7, 2, -0.10)
    features = {
        asset: _feature(
            return_1=-0.03,
            return_5=-0.02,
            return_20=-0.2,
            return_60=-0.2,
            return_120=-0.4,
            close=70.0,
            close_location=0.2,
            volume_ratio=0.5,
            trend_score=-1.0,
        )
        for asset in v28.ASSETS
    }
    weights, sleeve, counter = v28._daily_target(model, features, {}, "cash", 0)
    assert weights == {}
    assert sleeve == "cash"
    assert counter == 0


def test_protocol_has_five_non_overlapping_quarters() -> None:
    assert [window.name for window in v28.VALIDATION_WINDOWS] == [
        "2023-Q4",
        "2024-Q1",
        "2024-Q2",
        "2024-Q3",
        "2024-Q4",
    ]
    for left, right in zip(v28.VALIDATION_WINDOWS, v28.VALIDATION_WINDOWS[1:]):
        assert left.end < right.start

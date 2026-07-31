from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_monthly_ensemble_v30 as v30


def feature(**overrides: float) -> v30.Features:
    values = dict(
        return_1=0.02,
        return_3=0.03,
        return_5=0.05,
        return_10=0.08,
        return_20=0.10,
        return_60=0.20,
        return_120=0.30,
        return_180=0.40,
        volatility_20=0.02,
        sma_20=95.0,
        sma_50=90.0,
        sma_100=85.0,
        sma_200=80.0,
        close=100.0,
        close_location=0.75,
        volume_ratio=1.3,
        drawdown_20=-0.02,
        trend_score=10.0,
    )
    values.update(overrides)
    return v30.Features(**values)


def model(**overrides: object) -> v30.ModelSpec:
    values = dict(
        trend_sma=50,
        rebalance_days=5,
        top_n=2,
        maximum_exposure=0.20,
        recovery_threshold=-0.08,
        recovery_holding_days=2,
    )
    values.update(overrides)
    return v30.ModelSpec(**values)


def test_frozen_grid_has_exactly_64_unique_models() -> None:
    assert len(v30.MODEL_GRID) == 64
    assert len({item.model_id for item in v30.MODEL_GRID}) == 64
    assert {item.trend_sma for item in v30.MODEL_GRID} == {50, 100}
    assert {item.rebalance_days for item in v30.MODEL_GRID} == {5, 10}
    assert {item.top_n for item in v30.MODEL_GRID} == {1, 2}
    assert {item.maximum_exposure for item in v30.MODEL_GRID} == {0.10, 0.20}
    assert {item.recovery_threshold for item in v30.MODEL_GRID} == {-0.08, -0.12}
    assert {item.recovery_holding_days for item in v30.MODEL_GRID} == {2, 4}


def test_trend_sleeve_respects_20_percent_cap() -> None:
    payload = {
        asset: feature(trend_score=float(index + 1))
        for index, asset in enumerate(v30.ASSETS)
    }
    weights, selected, sleeve, age, remaining = v30._target(
        model(), payload, (), "cash", 0, 0, False
    )

    assert selected == ("DOGE", "LINK")
    assert sleeve == "trend"
    assert age == 0
    assert remaining == 0
    assert weights == {"DOGE": 0.10, "LINK": 0.10}


def test_fixed_recovery_holds_for_exact_additional_day() -> None:
    payload = {
        asset: feature(return_20=-0.1, return_60=-0.1, close=70.0)
        for asset in v30.ASSETS
    }
    payload["BTC"] = feature(return_5=-0.10, return_1=0.02, return_20=-0.1, return_60=-0.1, close=70.0, sma_200=80.0)
    payload["ETH"] = feature(return_5=-0.11, return_1=0.03, return_20=-0.1, return_60=-0.1, close=70.0, sma_200=80.0)

    weights, selected, sleeve, _, remaining = v30._target(
        model(recovery_holding_days=2), payload, (), "cash", 0, 0, False
    )
    assert sleeve == "recovery"
    assert selected == ("ETH", "BTC")
    assert remaining == 1
    assert sum(weights.values()) == 0.20

    held, held_assets, held_sleeve, _, held_remaining = v30._target(
        model(recovery_holding_days=2), payload, selected, sleeve, 0, remaining, False
    )
    assert held == weights
    assert held_assets == selected
    assert held_sleeve == "recovery"
    assert held_remaining == 0


def test_monthly_brake_forces_cash_even_during_recovery() -> None:
    payload = {asset: feature() for asset in v30.ASSETS}
    weights, selected, sleeve, trend_age, remaining = v30._target(
        model(), payload, ("BTC",), "recovery", 0, 3, True
    )
    assert weights == {}
    assert selected == ()
    assert sleeve == "cash"
    assert trend_age == 0
    assert remaining == 0


def test_defensive_btc_core_is_only_five_percent() -> None:
    payload = {
        asset: feature(return_20=-0.1, return_60=-0.1, return_120=-0.1, return_180=-0.1, close=70.0)
        for asset in v30.ASSETS
    }
    payload["BTC"] = feature(return_1=0.01, return_20=0.03, return_60=-0.02, return_120=-0.02, return_180=-0.02, close=100.0, sma_50=110.0, sma_200=90.0)
    weights, selected, sleeve, _, _ = v30._target(
        model(), payload, (), "cash", 0, 0, False
    )
    assert weights == {"BTC": 0.05}
    assert selected == ("BTC",)
    assert sleeve == "defensive_btc"


def test_five_verification_months_are_sequential() -> None:
    assert [window.name for window in v30.VERIFICATION_WINDOWS] == [
        "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"
    ]
    for left, right in zip(v30.VERIFICATION_WINDOWS, v30.VERIFICATION_WINDOWS[1:]):
        assert left.end + timedelta(days=1) == right.start


def test_discovery_has_54_complete_months() -> None:
    windows = v30.discovery_windows()
    assert len(windows) == 54
    assert windows[0].name == "2021-08"
    assert windows[-1].name == "2026-01"


def test_mode_is_paper_only_boundary() -> None:
    assert v30.MODE == "HISTORICAL_MONTHLY_TREND_RECOVERY_ENSEMBLE_ONLY"
    assert max(item.maximum_exposure for item in v30.MODEL_GRID) == 0.20
    assert v30.MONTHLY_LOSS_BRAKE == -0.015

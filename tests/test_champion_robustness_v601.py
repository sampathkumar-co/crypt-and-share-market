from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import champion_robustness_v601 as v601
from tradebot.research import historical_proxy_screen_v25 as v25
from tradebot.research import historical_yield_trend_v31 as v31


def _feature(value: float) -> v31.Features:
    return v31.Features(
        return_1=value,
        return_5=0.0,
        return_20=0.0,
        return_60=0.0,
        return_120=0.0,
        return_200=0.0,
        volatility_20=0.01,
        sma_50=100.0,
        sma_100=100.0,
        sma_200=100.0,
        close=100.0,
        drawdown_20=0.0,
        trend_score=value,
    )


def _bar(value: float) -> v25.HourlyBar:
    return v25.HourlyBar(open=value, high=value, low=value, close=value, volume=1.0)


def test_positive_concentration_uses_only_positive_intervals():
    assert v601._positive_concentration([0.2, -4.0, 0.3]) == 0.6
    assert v601._positive_concentration([-1.0, 0.0]) == 1.0


def test_one_day_signal_delay_changes_only_information_timing(monkeypatch):
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dates = [base + timedelta(days=index) for index in range(6)]
    bars = {
        "BTC": {day: _bar(value) for day, value in zip(dates, [100, 100, 100, 110, 110, 110], strict=True)},
        "ETH": {day: _bar(100.0) for day in dates},
    }
    features = {
        day: {"BTC": _feature(1.0 if index == 1 else -1.0), "ETH": _feature(-1.0)}
        for index, day in enumerate(dates)
    }
    cash_returns = {day: 0.0 for day in dates}
    model = v31.ModelSpec(
        sma_length=100,
        rebalance_days=1,
        top_n=1,
        maximum_exposure=0.10,
        volatility_target=0.02,
        drawdown_brake=0.20,
    )

    def target(_model, feature_map, _selected, _sleeve, _age):
        if feature_map["BTC"].return_1 > 0.0:
            return {"BTC": 0.10}, ("BTC",), "trend", 0
        return {}, (), "cash", 0

    monkeypatch.setattr(v601.v31, "_target", target)
    control = v601.simulate_diagnostic(
        model,
        bars,
        features,
        cash_returns,
        dates[2],
        dates[4],
        0.0,
        signal_lag_days=1,
    )
    delayed = v601.simulate_diagnostic(
        model,
        bars,
        features,
        cash_returns,
        dates[2],
        dates[4],
        0.0,
        signal_lag_days=2,
    )
    assert control.net_return > delayed.net_return
    assert delayed.net_return == 0.0
    assert control.maximum_positive_interval_share == 1.0


def test_invalid_signal_lag_fails_closed():
    model = v31.ModelSpec(100, 1, 1, 0.10, 0.02, 0.20)
    with __import__("pytest").raises(v601.ChampionRobustnessV601Error):
        v601.simulate_diagnostic(
            model,
            {},
            {},
            {},
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            0.0,
            signal_lag_days=0,
        )

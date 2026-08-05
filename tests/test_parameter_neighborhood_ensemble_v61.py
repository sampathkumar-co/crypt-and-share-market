from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_proxy_screen_v25 as v25
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import parameter_neighborhood_ensemble_v61 as v61


def _feature() -> v31.Features:
    return v31.Features(
        return_1=1.0,
        return_5=1.0,
        return_20=1.0,
        return_60=1.0,
        return_120=1.0,
        return_200=1.0,
        volatility_20=0.01,
        sma_50=90.0,
        sma_100=90.0,
        sma_200=90.0,
        close=100.0,
        drawdown_20=0.0,
        trend_score=1.0,
    )


def _bar(day: datetime, value: float) -> v25.HourlyBar:
    return v25.HourlyBar(
        hour=day,
        open=value,
        high=value,
        low=value,
        close=value,
        quote_volume=1.0,
        taker_buy_quote_volume=0.5,
    )


def test_frozen_member_set_is_complete_neighborhood():
    assert len(v61.MEMBERS) == 16
    assert {model.sma_length for model in v61.MEMBERS} == {100, 200}
    assert {model.top_n for model in v61.MEMBERS} == {1, 2}
    assert {model.volatility_target for model in v61.MEMBERS} == {0.02, 0.03}
    assert {model.drawdown_brake for model in v61.MEMBERS} == {0.10, 0.20}
    assert {model.rebalance_days for model in v61.MEMBERS} == {10}
    assert {model.maximum_exposure for model in v61.MEMBERS} == {0.10}


def test_identical_member_targets_reproduce_single_target(monkeypatch):
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dates = [base + timedelta(days=index) for index in range(6)]
    bars = {
        "BTC": {day: _bar(day, 100.0 + 10.0 * index) for index, day in enumerate(dates)},
        "ETH": {day: _bar(day, 100.0) for day in dates},
    }
    features = {day: {"BTC": _feature(), "ETH": _feature()} for day in dates}
    cash = {day: 0.0 for day in dates}

    def target(_model, _features, _selected, _sleeve, age):
        return {"BTC": 0.10}, ("BTC",), "trend", age + 1

    monkeypatch.setattr(v61.v31, "_target", target)
    result = v61.simulate_ensemble(
        bars,
        features,
        cash,
        dates[1],
        dates[4],
        0.0,
        signal_lag_days=1,
    )
    assert result.net_return > 0.0
    assert result.action_days >= 1
    assert result.maximum_drawdown == 0.0


def test_conservative_summary_uses_worse_return_and_higher_risk():
    template = {
        "standard": {
            "net_compounded_return": 0.30,
            "cash_compounded_return": 0.18,
            "window_returns": {str(year): 0.05 for year in range(2021, 2026)},
            "action_days": 40,
            "maximum_drawdown": 0.02,
            "maximum_positive_interval_share": 0.10,
            "maximum_positive_year_share": 0.30,
        },
        "stress": {
            "net_compounded_return": 0.25,
            "window_returns": {str(year): 0.04 for year in range(2021, 2026)},
        },
        "delayed": {"excess_compounded_return": 0.10},
    }
    other = {
        **template,
        "standard": {**template["standard"], "net_compounded_return": 0.28, "maximum_drawdown": 0.03},
        "stress": {**template["stress"], "net_compounded_return": 0.24},
        "delayed": {"excess_compounded_return": 0.08},
    }
    result = v61._conservative(template, other)
    assert result["standard_return"] == 0.28
    assert result["stress_return"] == 0.24
    assert result["maximum_drawdown"] == 0.03
    assert result["delayed_excess_over_cash"] == 0.08

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_yield_trend_v31 as v31


def _bar(day: datetime, price: float) -> v31.v25.HourlyBar:
    return v31.v25.HourlyBar(
        hour=day,
        open=price,
        high=price,
        low=price,
        close=price,
        quote_volume=1_000_000.0,
        taker_buy_quote_volume=500_000.0,
    )


def _feature() -> v31.Features:
    return v31.Features(
        return_1=0.01,
        return_5=0.03,
        return_20=0.10,
        return_60=0.20,
        return_120=0.30,
        return_200=0.40,
        volatility_20=0.02,
        sma_50=90.0,
        sma_100=85.0,
        sma_200=80.0,
        close=100.0,
        drawdown_20=-0.02,
        trend_score=10.0,
    )


def _model() -> v31.ModelSpec:
    return v31.ModelSpec(100, 5, 1, 0.20, 0.02, 0.10)


def test_full_cash_strategy_exactly_matches_cash_benchmark(monkeypatch) -> None:
    day = datetime(2021, 1, 2, tzinfo=timezone.utc)
    signal = day - timedelta(days=1)
    next_day = day + timedelta(days=1)
    bars = {
        asset: {day: _bar(day, 100.0), next_day: _bar(next_day, 100.0)}
        for asset in v31.ASSETS
    }
    features = {signal: {asset: _feature() for asset in v31.ASSETS}}
    cash_return = 0.0001

    monkeypatch.setattr(
        v31,
        "_target",
        lambda *args, **kwargs: ({}, (), "cash", 0),
    )
    result = v31.simulate(
        _model(),
        bars,
        features,
        {day: cash_return},
        day,
        day,
        0.002,
    )

    assert abs(result.net_return - cash_return) < 1e-15
    assert abs(result.cash_benchmark_return - cash_return) < 1e-15
    assert abs(result.excess_return) < 1e-15
    assert result.crypto_turnover == 0.0
    assert result.crypto_action_days == 0


def test_flat_crypto_overlay_loses_only_explicit_cost_relative_to_cash(
    monkeypatch,
) -> None:
    day = datetime(2021, 1, 2, tzinfo=timezone.utc)
    signal = day - timedelta(days=1)
    next_day = day + timedelta(days=1)
    bars = {
        asset: {day: _bar(day, 100.0), next_day: _bar(next_day, 100.0)}
        for asset in v31.ASSETS
    }
    features = {signal: {asset: _feature() for asset in v31.ASSETS}}
    cash_return = 0.0001

    monkeypatch.setattr(
        v31,
        "_target",
        lambda *args, **kwargs: ({"BTC": 0.20}, ("BTC",), "trend", 0),
    )
    result = v31.simulate(
        _model(),
        bars,
        features,
        {day: cash_return},
        day,
        day,
        0.002,
    )

    entry_cost = 0.5 * 0.002 * 0.20
    first_day_return = 0.80 * cash_return - entry_cost
    drifted_weight = 0.20 / (1.0 + first_day_return)
    exit_cost = 0.5 * 0.002 * drifted_weight
    expected = (1.0 + first_day_return) * (1.0 - exit_cost) - 1.0

    assert abs(result.net_return - expected) < 1e-15
    assert abs(result.cash_benchmark_return - cash_return) < 1e-15
    assert result.excess_return < 0.0
    assert abs(result.crypto_turnover - (0.20 + drifted_weight)) < 1e-15
    assert result.crypto_action_days == 1


def test_cash_rate_for_entry_day_cannot_use_same_day_observation() -> None:
    day = datetime(2021, 1, 4, tzinfo=timezone.utc)
    rates = {
        datetime(2021, 1, 1, tzinfo=timezone.utc): 0.01,
        day: 0.05,
    }
    returns = v31.build_daily_cash_returns(rates, [day])
    expected = 1.01 ** (1.0 / 365.0) - 1.0
    assert abs(returns[day] - expected) < 1e-15

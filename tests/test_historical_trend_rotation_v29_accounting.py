from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_trend_rotation_v29 as v29


def _bar(day: datetime, price: float) -> v29.v25.HourlyBar:
    return v29.v25.HourlyBar(
        hour=day,
        open=price,
        high=price,
        low=price,
        close=price,
        quote_volume=1_000_000.0,
        taker_buy_quote_volume=500_000.0,
    )


def _feature() -> v29.Features:
    return v29.Features(
        return_1=0.01,
        return_3=0.02,
        return_5=0.03,
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
        close_location=0.8,
        volume_ratio=1.2,
        drawdown_20=-0.02,
        trend_score=10.0,
    )


def test_one_day_flat_position_charges_entry_and_terminal_exit(monkeypatch) -> None:
    day = datetime(2025, 1, 2, tzinfo=timezone.utc)
    signal_day = day - timedelta(days=1)
    next_day = day + timedelta(days=1)
    bars = {
        asset: {day: _bar(day, 100.0), next_day: _bar(next_day, 100.0)}
        for asset in v29.ASSETS
    }
    features = {signal_day: {asset: _feature() for asset in v29.ASSETS}}

    def fixed_target(*args, **kwargs):
        return {"BTC": 0.30}, ("BTC",), "strong_trend", 0

    monkeypatch.setattr(v29, "_target", fixed_target)
    result = v29.simulate(
        v29.ModelSpec(80, 1.0 / 3.0, 5, 1, 0.30, 0.10),
        bars,
        features,
        day,
        day,
        0.002,
    )

    entry_cost = 0.0003
    drifted = 0.30 / (1.0 - entry_cost)
    drifted = min(0.30, drifted)
    exit_cost = 0.5 * 0.002 * drifted
    expected = (1.0 - entry_cost) * (1.0 - exit_cost) - 1.0
    assert abs(result.net_return - expected) < 1e-12
    assert abs(result.turnover - (0.30 + drifted)) < 1e-12
    assert result.non_cash_action_days == 1


def test_signal_day_precedes_entry_day() -> None:
    day = datetime(2025, 1, 2, tzinfo=timezone.utc)
    assert day - timedelta(days=1) < day
    assert v29.VERIFICATION_WINDOWS[0].start == datetime(
        2025, 1, 1, tzinfo=timezone.utc
    )

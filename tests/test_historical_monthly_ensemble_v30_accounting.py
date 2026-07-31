from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_monthly_ensemble_v30 as v30
from tradebot.research import historical_monthly_ensemble_v30_runner as runner


def _bar(day: datetime, price: float) -> v30.v25.HourlyBar:
    return v30.v25.HourlyBar(
        hour=day,
        open=price,
        high=price,
        low=price,
        close=price,
        quote_volume=1_000_000.0,
        taker_buy_quote_volume=500_000.0,
    )


def _feature(**overrides: float) -> v30.Features:
    values = dict(
        return_1=0.01,
        return_3=0.02,
        return_5=0.03,
        return_10=0.05,
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
        close_location=0.8,
        volume_ratio=1.3,
        drawdown_20=-0.02,
        trend_score=10.0,
    )
    values.update(overrides)
    return v30.Features(**values)


def _model() -> v30.ModelSpec:
    return v30.ModelSpec(50, 5, 1, 0.20, -0.08, 2)


def test_flat_one_day_position_charges_entry_and_natural_drift_exit(
    monkeypatch,
) -> None:
    day = datetime(2026, 2, 2, tzinfo=timezone.utc)
    signal_day = day - timedelta(days=1)
    next_day = day + timedelta(days=1)
    bars = {
        asset: {day: _bar(day, 100.0), next_day: _bar(next_day, 100.0)}
        for asset in v30.ASSETS
    }
    features = {signal_day: {asset: _feature() for asset in v30.ASSETS}}

    def fixed_target(*args, **kwargs):
        return {"BTC": 0.20}, ("BTC",), "trend", 0, 0

    monkeypatch.setattr(runner, "guarded_target", fixed_target)
    result = runner.simulate_guarded(
        _model(), bars, features, day, day, 0.002
    )

    entry_cost = 0.0002
    drifted = 0.20 / (1.0 - entry_cost)
    exit_cost = 0.5 * 0.002 * drifted
    expected = (1.0 - entry_cost) * (1.0 - exit_cost) - 1.0
    assert abs(result.net_return - expected) < 1e-12
    assert abs(result.turnover - (0.20 + drifted)) < 1e-12
    assert result.non_cash_action_days == 1


def test_loss_brake_uses_only_prior_realized_return(monkeypatch) -> None:
    days = [
        datetime(2026, 2, 2, tzinfo=timezone.utc) + timedelta(days=index)
        for index in range(3)
    ]
    bars = {
        asset: {
            days[0]: _bar(days[0], 100.0),
            days[1]: _bar(days[1], 90.0),
            days[2]: _bar(days[2], 90.0),
        }
        for asset in v30.ASSETS
    }
    features = {
        days[0] - timedelta(days=1): {
            asset: _feature() for asset in v30.ASSETS
        },
        days[1] - timedelta(days=1): {
            asset: _feature() for asset in v30.ASSETS
        },
    }
    calls: list[bool] = []

    def target(model, payload, selected, sleeve, age, recovery, brake):
        calls.append(brake)
        if brake:
            return {}, (), "cash", 0, 0
        return {"BTC": 0.20}, ("BTC",), "trend", 0, 0

    monkeypatch.setattr(runner, "guarded_target", target)
    result = runner.simulate_guarded(
        _model(), bars, features, days[0], days[1], 0.002
    )

    assert calls == [False, True]
    assert result.brake_triggered is True


def test_expired_recovery_forces_cash_exit_before_new_signal() -> None:
    payload = {asset: _feature() for asset in v30.ASSETS}
    result = runner.guarded_target(
        _model(), payload, ("BTC",), "recovery", 0, 0, False
    )
    assert result == ({}, (), "cash", 0, 0)


def test_trend_regime_without_eligible_leader_cannot_open_recovery() -> None:
    payload = {
        asset: _feature(
            return_120=-0.01,
            return_180=-0.01,
            return_5=-0.10,
            return_1=0.02,
            drawdown_20=-0.12,
        )
        for asset in v30.ASSETS
    }
    assert runner._trend_mode(_model(), payload) is True
    weights, assets, sleeve, age, remaining = runner.guarded_target(
        _model(), payload, (), "cash", 0, 0, False
    )
    assert weights == {}
    assert assets == ()
    assert sleeve == "cash"
    assert age == 0
    assert remaining == 0


def test_runner_restores_original_simulator(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    def fake_run(*, max_workers: int):
        captured["patched"] = v30.simulate is runner.simulate_guarded
        return {
            "fingerprints": {
                "protocol_sha256": "a",
                "implementation_sha256": "b",
                "chosen_model_sha256": "c",
            },
            "report_sha256": "stale",
        }

    original = v30.simulate
    monkeypatch.setattr(v30, "run_ensemble", fake_run)
    report = runner.run_guarded_ensemble(max_workers=3)

    assert captured["patched"] is True
    assert v30.simulate is original
    assert report["execution_policy"] == runner.EXECUTION_POLICY
    assert report["fingerprints"]["runner_sha256"]
    assert report["report_sha256"] != "stale"

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_rotation_v28 as v28
from tradebot.research import historical_rotation_v28_runner as runner


def _bar(day: datetime, open_price: float) -> v28.v25.HourlyBar:
    return v28.v25.HourlyBar(
        hour=day,
        open=open_price,
        high=open_price,
        low=open_price,
        close=open_price,
        quote_volume=1_000_000.0,
        taker_buy_quote_volume=500_000.0,
    )


def _feature() -> v28.AssetFeatures:
    return v28.AssetFeatures(
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
        close_location=0.8,
        volume_ratio=1.5,
        trend_score=10.0,
    )


def test_closed_simulation_charges_entry_and_final_exit(monkeypatch) -> None:
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    signal = start - timedelta(days=1)
    next_day = start + timedelta(days=1)
    bars = {
        asset: {
            start: _bar(start, 100.0),
            next_day: _bar(next_day, 100.0),
        }
        for asset in v28.ASSETS
    }
    features = {signal: {asset: _feature() for asset in v28.ASSETS}}

    def fixed_target(*args, **kwargs):
        return {"BTC": 0.30}, "trend", 0

    monkeypatch.setattr(v28, "_daily_target", fixed_target)
    result = runner.simulate_closed(
        v28.ModelSpec(80, 0.4, 5, 1, -0.06),
        bars,
        features,
        start,
        start,
        0.002,
    )

    assert abs(result.turnover - 0.60) < 1e-12
    assert abs(result.net_return - ((1.0 - 0.0003) * (1.0 - 0.0003) - 1.0)) < 1e-12
    assert result.sleeve_contribution["final_liquidation"] == -0.0003
    assert result.non_cash_action_days == 1


def test_guard_restores_original_simulator(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    def fake_run_rotation(*, max_workers: int):
        captured["patched"] = v28.simulate is runner.simulate_closed
        return {
            "fingerprints": {
                "protocol_sha256": "a",
                "implementation_sha256": "b",
                "chosen_model_sha256": "c",
            },
            "report_sha256": "stale",
        }

    original = v28.simulate
    monkeypatch.setattr(v28, "run_rotation", fake_run_rotation)
    report = runner.run_guarded_rotation(max_workers=3)

    assert captured["patched"] is True
    assert v28.simulate is original
    assert report["accounting_policy"] == (
        "independent_windows_start_and_end_in_cash"
    )
    assert report["fingerprints"]["runner_sha256"]
    assert report["report_sha256"] != "stale"

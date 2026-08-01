from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tradebot.research import learned_daily_multihorizon_v41 as v41


def synthetic_bars(count: int = 1000):
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    result = {asset: {} for asset in v41.ASSETS}
    for pos, asset in enumerate(v41.ASSETS):
        price = 100.0 + pos * 20.0
        for day in range(count):
            stamp = start + timedelta(days=day)
            drift = 0.0004 + 0.0001 * np.sin(day / 17.0 + pos)
            open_ = price
            close = open_ * (1.0 + drift)
            high = max(open_, close) * 1.01
            low = min(open_, close) * 0.99
            volume = 1000.0 + day + pos * 100.0
            result[asset][stamp] = v41.Bar(stamp, low, high, open_, close, volume)
            price = close
    return result


def small_dataset(days: int = 15):
    dates = [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i) for i in range(days)]
    row_dates = [date for date in dates for _ in v41.ASSETS]
    assets = [asset for _ in dates for asset in v41.ASSETS]
    rows = len(row_dates)
    return v41.Dataset(
        X=np.zeros((rows, 3)),
        returns={1: np.full(rows, 0.01), 3: np.full(rows, 0.02), 7: np.full(rows, 0.03)},
        opportunities={h: np.ones(rows, dtype=int) for h in v41.HORIZONS},
        downside3=np.zeros(rows, dtype=int),
        regimes=np.zeros(rows, dtype=int),
        dates=row_dates,
        assets=assets,
        feature_names=["a", "b", "c"],
        quote_volume_30=np.full(rows, 1_000_000.0),
    )


def fake_bundle() -> v41.Bundle:
    return v41.Bundle(
        return_models={}, opportunity_models={}, downside_models=[], regime_model=None,
        config={"learning_rate": 0.04, "max_leaf_nodes": 15, "max_iter": 120},
        opportunity_threshold=0.35,
        required_positive_horizons=2,
        uncertainty_threshold=1.0,
        liquidity_threshold=1.0,
        feature_names=["a", "b", "c"],
    )


def fake_predictions(dataset: v41.Dataset):
    rows = len(dataset.dates)
    return {
        "returns": {1: np.full(rows, 0.01), 3: np.full(rows, 0.02), 7: np.full(rows, 0.03)},
        "opportunities": {h: np.full(rows, 0.9) for h in v41.HORIZONS},
        "downside3": np.full(rows, 0.1),
        "disagreement": np.zeros(rows),
        "regime": np.zeros(rows, dtype=int),
    }


def test_model_grid_is_frozen():
    assert v41.model_grid() == [
        {"learning_rate": 0.04, "max_leaf_nodes": 15, "max_iter": 120},
        {"learning_rate": 0.04, "max_leaf_nodes": 31, "max_iter": 120},
        {"learning_rate": 0.08, "max_leaf_nodes": 15, "max_iter": 120},
        {"learning_rate": 0.08, "max_leaf_nodes": 31, "max_iter": 120},
    ]


def test_dataset_uses_exact_daily_history_and_next_open_labels():
    dataset = v41.build_dataset(synthetic_bars())
    assert dataset.X.shape[1] == len(v41.feature_names())
    assert len(set(dataset.dates)) > 700
    assert all(len(dataset.returns[h]) == len(dataset.dates) for h in v41.HORIZONS)
    train, calibration, test = v41.chronological_masks(dataset)
    assert not np.any(train & calibration)
    assert not np.any(calibration & test)
    assert int(train.sum()) > int(test.sum()) > 0


def test_parse_rejects_conflicting_duplicates():
    stamp = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    payload = json.dumps([
        [stamp, 1, 2, 1.2, 1.5, 10],
        [stamp, 1, 2, 1.2, 1.6, 10],
    ]).encode()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(v41.LearnedDailyV41Error):
        v41.parse_candles(payload, start, start)


def test_simulator_rebalances_only_every_third_day_and_liquidates():
    dataset = small_dataset()
    mask = np.ones(len(dataset.dates), dtype=bool)
    summary = v41.simulate(
        dataset, mask, fake_bundle(), fake_predictions(dataset), one_way_cost=0.001
    )
    assert summary["target_changing_actions"] >= 2
    assert summary["selected_assets"] == ["ADA", "XRP"] or len(summary["selected_assets"]) == 2
    assert summary["turnover"] > 0.0
    assert summary["terminal_equity_before_liquidation"] > 0.0


def test_panic_forces_cash_before_scheduled_rebalance():
    dataset = small_dataset(8)
    predictions = fake_predictions(dataset)
    panic_date = sorted(set(dataset.dates))[1]
    for index, date in enumerate(dataset.dates):
        if date == panic_date:
            predictions["regime"][index] = 2
    summary = v41.simulate(
        dataset, np.ones(len(dataset.dates), dtype=bool), fake_bundle(), predictions,
        one_way_cost=0.001,
    )
    assert summary["target_changing_actions"] >= 2
    assert summary["maximum_drawdown"] <= 0.10


def test_current_reasoning_never_exceeds_ten_percent(monkeypatch):
    bundle = fake_bundle()
    rows = len(v41.ASSETS)
    monkeypatch.setattr(v41, "latest_feature_matrix", lambda bars: (
        datetime(2026, 1, 1, tzinfo=timezone.utc), np.zeros((rows, 3)),
        np.full(rows, 1_000_000.0),
    ))
    monkeypatch.setattr(v41, "predict_bundle", lambda bundle, X: {
        "returns": {h: np.full(rows, 0.02) for h in v41.HORIZONS},
        "opportunities": {h: np.full(rows, 0.9) for h in v41.HORIZONS},
        "downside3": np.full(rows, 0.1),
        "disagreement": np.zeros(rows),
        "regime": np.zeros(rows, dtype=int),
    })
    report = v41.current_reasoning(bundle, {})
    assert sum(report["target_weights"].values()) <= 0.10
    assert report["minimum_cash_weight"] >= 0.90
    assert report["authorizes_trading"] is False


def test_shadow_smoke_uses_frozen_target_and_liquidates():
    from tradebot.research import shadow_daily_v41 as shadow

    historical = {
        "report_sha256": "abc",
        "evaluation": {"status": "NOT_YET_HISTORICAL_BREAKTHROUGH"},
        "current_reasoning": {"target_weights": {"BTC": 0.05}},
    }
    ticks = iter([
        {asset: 100.0 for asset in v41.ASSETS},
        {asset: 101.0 for asset in v41.ASSETS},
        {asset: 102.0 for asset in v41.ASSETS},
    ])
    times = iter([
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 0, 3, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 0, 4, tzinfo=timezone.utc),
    ])
    report = shadow.run_smoke(
        historical,
        duration_seconds=2,
        poll_seconds=1,
        price_fetcher=lambda: next(ticks),
        now_fn=lambda: next(times),
        sleep_fn=lambda _: None,
    )
    assert report["smoke_passed"] is True
    assert report["final_positions"] == {}
    assert report["costs_paid"] > 0.0
    assert report["profitability_proven"] is False

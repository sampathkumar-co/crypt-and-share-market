from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tradebot.research import regime_diversified_utility_v45 as model
from tradebot.research.regime_ranking_v42 import Dataset


def manual_dataset(start: datetime, day_count: int) -> Dataset:
    dates: list[datetime] = []
    assets: list[str] = []
    returns: list[float] = []
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for asset in model.ASSETS:
            dates.append(stamp)
            assets.append(asset)
            returns.append(0.01 if asset in {"BTC", "ETH"} else 0.0)
    size = len(dates)
    return Dataset(
        X=np.zeros((size, 2), dtype=float),
        return1=np.asarray(returns),
        return3=np.zeros(size),
        return7=np.zeros(size),
        rank3=np.zeros(size),
        meta=np.ones(size, dtype=int),
        downside3=np.zeros(size, dtype=int),
        regimes=np.zeros(size, dtype=int),
        dates=dates,
        assets=assets,
        feature_names=["x", "y"],
    )


def config(**updates) -> model.Config:
    values = {
        "panic_threshold": 0.55,
        "utility_threshold": 0.004,
        "q20_floor": -0.02,
        "dispersion_quantile": 0.75,
        "dispersion_threshold": 1.0,
        "downside_exclusion_count": 0,
        "top_n": 1,
        "entropy_penalty": 0.0,
        "dispersion_penalty": 0.5,
    }
    values.update(updates)
    return model.Config(**values)


def cash_history(start: datetime) -> model.v44.CashRateHistory:
    prior = start - timedelta(days=1)
    return model.v44.CashRateHistory(
        annual_rates={prior: 0.05},
        source={
            "provider": model.v44.CASH_PROVIDER,
            "series": model.v44.CASH_SERIES,
            "observation_count": 1,
            "first_date": prior.date().isoformat(),
            "last_date": prior.date().isoformat(),
        },
    )


def test_normalized_entropy_has_expected_limits():
    assert model.normalized_entropy(np.asarray([1.0, 0.0])) == 0.0
    assert model.normalized_entropy(np.asarray([0.5, 0.5])) == pytest.approx(1.0)
    assert 0.0 < model.normalized_entropy(np.asarray([0.8, 0.2])) < 1.0


def test_mixed_metrics_use_probability_weights_and_attribution(monkeypatch):
    bundle = SimpleNamespace(specialists={0: object(), 1: object()})
    predictions = {"specialists": {0: "chop", 1: "trend"}}
    context = {
        "mean_probabilities": np.asarray([0.75, 0.25, 0.0, 0.0]),
        "std_probabilities": np.zeros(4),
    }

    def metrics(specialist, _index, _std):
        utility = 0.02 if specialist == "chop" else 0.04
        return {
            "return3": utility,
            "return7": utility + 0.01,
            "q20": utility - 0.01,
            "rank": 0.6 if specialist == "chop" else 0.8,
            "disagreement": 0.001,
            "utility": utility,
        }

    monkeypatch.setattr(model.v43, "candidate_metrics", metrics)
    result = model.mixed_candidate_metrics(
        bundle,
        predictions,
        context,
        0,
        entropy_penalty=0.0,
        dispersion_penalty=0.0,
    )
    assert result["base_utility"] == pytest.approx(0.025)
    assert result["return3"] == pytest.approx(0.025)
    assert result["rank"] == pytest.approx(0.65)
    assert result["attribution_regime"] == 0
    assert result["cross_regime_dispersion"] > 0.0


def test_downside_veto_excludes_lowest_q20_asset(monkeypatch):
    dataset = manual_dataset(
        datetime(2025, 7, 1, tzinfo=timezone.utc),
        1,
    )
    mask = np.ones(len(dataset.X), dtype=bool)
    stamp = dataset.dates[0]
    context = {
        "indexes": list(range(len(dataset.X))),
        "mean_probabilities": np.asarray([0.8, 0.2, 0.0, 0.0]),
        "std_probabilities": np.zeros(4),
    }
    monkeypatch.setattr(
        model.v43,
        "date_contexts",
        lambda *_args: {stamp: context},
    )
    q20 = {"ADA": -0.05, "BTC": 0.01, "ETH": 0.02, "SOL": 0.00, "XRP": 0.005}
    rank = {"ADA": 1.0, "BTC": 0.8, "ETH": 0.9, "SOL": 0.7, "XRP": 0.6}

    def mixed(_bundle, _predictions, _context, index, **_kwargs):
        asset = dataset.assets[index]
        return {
            "return3": 0.03,
            "return7": 0.04,
            "q20": q20[asset],
            "rank": rank[asset],
            "utility": 0.02,
            "cross_regime_dispersion": 0.0,
            "attribution_regime": 0,
        }

    monkeypatch.setattr(model, "mixed_candidate_metrics", mixed)
    decision = model.decisions_by_date(
        dataset,
        mask,
        SimpleNamespace(specialists={0: object(), 1: object()}),
        {},
        config(downside_exclusion_count=1, top_n=1),
    )[stamp]
    assert decision["excluded_assets"] == ["ADA"]
    assert [dataset.assets[index] for index in decision["selected"]] == ["ETH"]


def test_calibration_key_prioritizes_worst_block():
    def summary(value: float) -> dict:
        return {
            "net_return": value,
            "maximum_drawdown": 0.01,
            "turnover": 0.2,
            "target_changing_actions": 3,
            "regime_contribution": {
                "chop": 0.01,
                "trend": 0.005,
                "panic": 0.0,
                "recovery": 0.0,
            },
        }

    unstable = [summary(0.02), summary(0.02), summary(-0.001)]
    robust = [summary(0.003), summary(0.003), summary(0.003)]
    assert model.calibration_key(robust, config()) > model.calibration_key(
        unstable,
        config(),
    )


def test_yield_simulation_keeps_exposure_and_regime_attribution(monkeypatch):
    start = datetime(2025, 7, 2, tzinfo=timezone.utc)
    dataset = manual_dataset(start, 4)
    mask = np.ones(len(dataset.X), dtype=bool)

    def decisions(dataset, mask, *_args):
        result = {}
        for stamp in sorted({dataset.dates[index] for index in np.flatnonzero(mask)}):
            indexes = [
                index for index in np.flatnonzero(mask)
                if dataset.dates[index] == stamp
            ]
            btc = next(index for index in indexes if dataset.assets[index] == "BTC")
            eth = next(index for index in indexes if dataset.assets[index] == "ETH")
            result[stamp] = {
                "panic": False,
                "regime": None,
                "selected": [btc, eth],
                "selected_regimes": {"BTC": 0, "ETH": 1},
                "candidate_count": 2,
                "excluded_assets": [],
                "panic_probability": 0.0,
            }
        return result

    monkeypatch.setattr(model, "decisions_by_date", decisions)
    result = model.simulate(
        dataset,
        mask,
        object(),
        {},
        cash_history(start),
        config(top_n=2),
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert result["maximum_target_exposure"] <= 0.1000001
    assert result["selected_assets"] == ["BTC", "ETH"]
    assert result["cash_contribution"] > 0.0
    assert result["regime_contribution"]["chop"] > 0.0
    assert result["regime_contribution"]["trend"] > 0.0


def test_calibration_blocks_are_presealed_and_contiguous():
    prior_end = None
    for _, start, end in model.CALIBRATION_BLOCKS:
        if prior_end is not None:
            assert start == prior_end + timedelta(days=1)
        assert end >= start
        prior_end = end
    assert model.CALIBRATION_BLOCKS[0][1] == model.v43.day("2025-07-01")
    assert model.CALIBRATION_BLOCKS[-1][2] == model.v43.day("2025-09-30")
    assert model.CALIBRATION_BLOCKS[-1][2] < model.v43.SEALED_WINDOWS[0][1]


def test_source_keeps_paper_only_boundary():
    text = Path(model.__file__).read_text(encoding="utf-8").lower()
    assert "private_key" not in text
    assert "create_order" not in text
    assert "place_order" not in text
    assert '"authorizes_trading": false' in text

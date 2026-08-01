from __future__ import annotations

from datetime import datetime, timedelta, timezone
import warnings

import joblib
import numpy as np
import pytest

from tradebot.research import distributional_utility_v43 as model
from tradebot.research.regime_ranking_v42 import Dataset


def manual_dataset(
    start: datetime,
    day_count: int,
) -> Dataset:
    dates: list[datetime] = []
    assets: list[str] = []
    returns: list[float] = []
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for asset in model.ASSETS:
            dates.append(stamp)
            assets.append(asset)
            returns.append(0.01 if asset == "BTC" else 0.0)
    size = len(dates)
    return Dataset(
        X=np.zeros((size, 2), dtype=float),
        return1=np.asarray(returns),
        return3=np.full(size, 0.02),
        return7=np.full(size, 0.03),
        rank3=np.linspace(0.0, 1.0, size),
        meta=np.ones(size, dtype=int),
        downside3=np.zeros(size, dtype=int),
        regimes=np.zeros(size, dtype=int),
        dates=dates,
        assets=assets,
        feature_names=["x", "y"],
    )


class ProbabilityModel:
    def __init__(self, classes, probabilities):
        self.classes_ = np.asarray(classes)
        self.probabilities = np.asarray(probabilities, dtype=float)

    def predict_proba(self, X):
        return np.tile(self.probabilities, (len(X), 1))


class ConstantRegressor:
    def __init__(self, value: float):
        self.value = value

    def predict(self, X):
        return np.full(len(X), self.value, dtype=float)


def fake_member(
    *,
    return3: float = 0.03,
    return7: float = 0.04,
    q20: float = 0.01,
    rank: float = 0.8,
    name: str = "full",
) -> model.Member:
    return model.Member(
        ConstantRegressor(return3),
        ConstantRegressor(return7),
        ConstantRegressor(q20),
        ConstantRegressor(rank),
        name,
    )


def fake_bundle(top_n: int = 1) -> model.Bundle:
    specialist = model.Specialist([
        fake_member(name="full"),
        fake_member(return3=0.028, rank=0.75, name="days720"),
    ])
    return model.Bundle(
        specialists={0: specialist},
        regime_models=[
            ProbabilityModel([0, 2], [0.8, 0.2]),
            ProbabilityModel([0, 2], [0.7, 0.3]),
        ],
        regime_window_names=["full", "days720"],
        config={"learning_rate": 0.04, "max_leaf_nodes": 15, "max_iter": 120},
        panic_threshold=0.55,
        utility_threshold=0.004,
        q20_floor=-0.02,
        top_n=top_n,
        disagreement_quantile=0.75,
        disagreement_threshold=1.0,
        feature_names=["x", "y"],
    )


def test_recency_masks_use_frozen_calendar_starts():
    dataset = manual_dataset(
        datetime(2023, 7, 1, tzinfo=timezone.utc),
        731,
    )
    masks = model.recency_masks(dataset)
    dates = np.asarray(dataset.dates, dtype=object)
    assert min(dates[masks["days720"]]) == model.day("2023-07-12")
    assert min(dates[masks["days360"]]) == model.day("2024-07-06")
    assert max(dates[masks["full"]]) == model.TRAIN_END


def test_aligned_probabilities_fill_absent_classes():
    fitted = ProbabilityModel([0, 2], [0.75, 0.25])
    values = model.aligned_probabilities(fitted, np.zeros((3, 2)))
    assert values.shape == (3, 4)
    assert np.allclose(values[:, 0], 0.75)
    assert np.allclose(values[:, 1], 0.0)
    assert np.allclose(values[:, 2], 0.25)
    assert np.allclose(values[:, 3], 0.0)


def test_date_level_regime_averages_all_assets():
    dataset = manual_dataset(
        datetime(2025, 7, 1, tzinfo=timezone.utc),
        1,
    )
    bundle = fake_bundle()
    predictions = model.predict_components(bundle, dataset.X)
    mask = np.ones(len(dataset.X), dtype=bool)
    context = next(iter(
        model.date_contexts(dataset, mask, bundle, predictions).values()
    ))
    assert context["regime"] == 0
    assert context["mean_probabilities"][2] == pytest.approx(0.25)
    assert context["std_probabilities"][0] == pytest.approx(0.05)


def test_candidate_utility_matches_frozen_formula():
    specialist = {
        "return3": np.asarray([0.03]),
        "return7": np.asarray([0.04]),
        "q20": np.asarray([0.01]),
        "rank": np.asarray([0.80]),
        "std_return3": np.asarray([0.002]),
        "std_return7": np.asarray([0.003]),
        "std_q20": np.asarray([0.004]),
        "std_rank": np.asarray([0.05]),
    }
    metrics = model.candidate_metrics(specialist, 0, 0.10)
    disagreement = np.sqrt(
        0.002**2 + 0.003**2 + 0.004**2
        + (0.01 * 0.05) ** 2 + (0.01 * 0.10) ** 2
    )
    expected = (
        0.55 * 0.03 + 0.25 * 0.04 + 0.20 * 0.01
        + 0.01 * 0.30 - 0.50 * disagreement
    )
    assert metrics["disagreement"] == pytest.approx(disagreement)
    assert metrics["utility"] == pytest.approx(expected)


def manual_predictions(dataset: Dataset):
    size = len(dataset.X)
    ranks = {
        "BTC": 0.90, "ETH": 1.00, "SOL": 0.70,
        "XRP": 0.50, "ADA": 0.30,
    }
    return {
        "regime_members": np.asarray([
            np.tile([0.8, 0.0, 0.2, 0.0], (size, 1)),
            np.tile([0.7, 0.0, 0.3, 0.0], (size, 1)),
        ]),
        "specialists": {
            0: {
                "return3": np.full(size, 0.03),
                "return7": np.full(size, 0.04),
                "q20": np.full(size, 0.01),
                "rank": np.asarray([ranks[a] for a in dataset.assets]),
                "std_return3": np.full(size, 0.001),
                "std_return7": np.full(size, 0.001),
                "std_q20": np.full(size, 0.001),
                "std_rank": np.full(size, 0.01),
            }
        },
    }


def test_rank_prediction_is_primary_candidate_key():
    dataset = manual_dataset(
        datetime(2025, 7, 1, tzinfo=timezone.utc),
        1,
    )
    bundle = fake_bundle(top_n=1)
    predictions = manual_predictions(dataset)
    mask = np.ones(len(dataset.X), dtype=bool)
    decision = next(iter(
        model.decisions_by_date(
            dataset, mask, bundle, predictions
        ).values()
    ))
    assert [
        dataset.assets[index] for index in decision["selected"]
    ] == ["ETH"]


def test_three_day_cadence_and_target_exposure():
    dataset = manual_dataset(
        datetime(2025, 7, 1, tzinfo=timezone.utc),
        6,
    )
    bundle = fake_bundle(top_n=2)
    predictions = manual_predictions(dataset)
    mask = np.ones(len(dataset.X), dtype=bool)
    summary = model.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert summary["target_changing_actions"] == 2
    assert summary["maximum_target_exposure"] <= 0.1000001
    assert summary["selected_assets"] == ["BTC", "ETH"]


def test_panic_exits_before_scheduled_rebalance():
    dataset = manual_dataset(
        datetime(2025, 7, 1, tzinfo=timezone.utc),
        3,
    )
    bundle = fake_bundle(top_n=1)
    predictions = manual_predictions(dataset)
    panic_date = sorted(set(dataset.dates))[1]
    for member in predictions["regime_members"]:
        for index, stamp in enumerate(dataset.dates):
            if stamp == panic_date:
                member[index] = [0.1, 0.0, 0.9, 0.0]
    mask = np.ones(len(dataset.X), dtype=bool)
    summary = model.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert summary["target_changing_actions"] == 2
    assert summary["terminal_equity_before_liquidation"] < 1.0


def test_portable_bundle_state_roundtrip(tmp_path):
    bundle = fake_bundle(top_n=2)
    path = tmp_path / "bundle.joblib"
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Setting the shape on a NumPy array has been deprecated",
            category=DeprecationWarning,
        )
        model.save_bundle(path, bundle)
        raw = joblib.load(path)
    assert isinstance(raw["bundle"], dict)
    restored = model.bundle_from_state(raw["bundle"])
    assert restored.top_n == 2
    assert sorted(restored.specialists) == [0]
    assert [
        member.window_name
        for member in restored.specialists[0].members
    ] == ["full", "days720"]


def test_sealed_windows_are_contiguous_and_nonoverlapping():
    prior_end = None
    total_days = 0
    for _, start, end in model.SEALED_WINDOWS:
        if prior_end is not None:
            assert start == prior_end + timedelta(days=1)
        assert end >= start
        total_days += (end - start).days + 1
        prior_end = end
    assert model.SEALED_WINDOWS[0][1] == model.day("2025-10-01")
    assert model.SEALED_WINDOWS[-1][2] == model.day("2026-06-30")
    assert total_days == 273


def test_quantile_member_fits_with_installed_sklearn():
    dataset = manual_dataset(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        60,
    )
    dataset.X[:, 0] = np.linspace(-1.0, 1.0, len(dataset.X))
    mask = np.ones(len(dataset.X), dtype=bool)
    member = model._fit_member(
        dataset,
        mask,
        {"learning_rate": 0.04, "max_leaf_nodes": 15, "max_iter": 20},
        "test",
    )
    values = member.q20_model.predict(dataset.X[:5])
    assert values.shape == (5,)
    assert np.all(np.isfinite(values))

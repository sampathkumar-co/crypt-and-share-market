from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tradebot.research import walk_forward_selective_veto_v46 as model
from tradebot.research.regime_ranking_v42 import Dataset


def manual_dataset(start: datetime, day_count: int) -> Dataset:
    dates: list[datetime] = []
    assets: list[str] = []
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for asset in model.ASSETS:
            dates.append(stamp)
            assets.append(asset)
    size = len(dates)
    return Dataset(
        X=np.zeros((size, 2), dtype=float),
        return1=np.zeros(size),
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


def test_walk_forward_dates_are_ordered_and_nonoverlapping():
    prior_validation_end = None
    for fold in model.WALK_FORWARD_FOLDS:
        assert fold.training_end < fold.base_calibration_start
        assert fold.base_calibration_start <= fold.base_calibration_end
        assert fold.base_calibration_end < fold.validation_start
        assert fold.validation_start <= fold.validation_end
        if prior_validation_end is not None:
            assert fold.validation_start == prior_validation_end + timedelta(days=1)
        prior_validation_end = fold.validation_end
    assert model.WALK_FORWARD_FOLDS[-1].validation_end < model.v43.SEALED_WINDOWS[0][1]


def test_dynamic_recency_masks_end_at_requested_training_date():
    dataset = manual_dataset(
        datetime(2022, 1, 1, tzinfo=timezone.utc),
        1000,
    )
    training_end = datetime(2024, 6, 30, tzinfo=timezone.utc)
    masks = model.recency_masks_at(dataset, training_end)
    dates = np.asarray(dataset.dates, dtype=object)
    assert max(dates[masks["full"]]) == training_end
    assert min(dates[masks["days360"]]) == training_end - timedelta(days=359)
    assert min(dates[masks["days720"]]) == training_end - timedelta(days=719)


def test_disabled_veto_reproduces_baseline_decisions(monkeypatch):
    dataset = manual_dataset(
        datetime(2025, 7, 1, tzinfo=timezone.utc),
        1,
    )
    stamp = dataset.dates[0]
    baseline = {
        stamp: {
            "regime": 0,
            "selected": [0],
            "candidate_count": 2,
            "panic_probability": 0.1,
        }
    }
    monkeypatch.setattr(
        model.v43,
        "decisions_by_date",
        lambda *_args: baseline,
    )
    result = model.selective_decisions_by_date(
        dataset,
        np.ones(len(dataset.X), dtype=bool),
        object(),
        {},
        model.DISABLED_VETO,
        calibrated_dispersion_thresholds={0.75: 0.1, 0.90: 0.2},
    )
    assert result[stamp]["selected"] == baseline[stamp]["selected"]
    assert result[stamp]["selected_regimes"] == {"BTC": 0}
    assert result[stamp]["vetoed_assets"] == []


def test_active_veto_only_removes_baseline_selection(monkeypatch):
    dataset = manual_dataset(
        datetime(2025, 7, 1, tzinfo=timezone.utc),
        1,
    )
    stamp = dataset.dates[0]
    mask = np.ones(len(dataset.X), dtype=bool)
    baseline = {
        stamp: {
            "regime": 0,
            "selected": [0, 1],
            "candidate_count": 5,
            "panic_probability": 0.1,
        }
    }
    context = {
        "indexes": list(range(5)),
        "regime": 0,
        "mean_probabilities": np.asarray([0.8, 0.2, 0.0, 0.0]),
        "std_probabilities": np.zeros(4),
    }
    monkeypatch.setattr(model.v43, "decisions_by_date", lambda *_args: baseline)
    monkeypatch.setattr(model.v43, "date_contexts", lambda *_args: {stamp: context})
    q20 = {0: -0.04, 1: 0.01, 2: 0.0, 3: 0.0, 4: 0.0}

    def metrics(_specialist, index, _std):
        return {
            "return3": 0.03,
            "return7": 0.04,
            "q20": q20[index],
            "rank": 0.8,
            "disagreement": 0.001,
            "utility": 0.02,
        }

    monkeypatch.setattr(model.v43, "candidate_metrics", metrics)
    monkeypatch.setattr(
        model.v45,
        "mixed_candidate_metrics",
        lambda *_args, **_kwargs: {"cross_regime_dispersion": 0.0},
    )
    bundle = SimpleNamespace(
        specialists={0: object(), 1: object()},
        utility_threshold=0.012,
    )
    result = model.selective_decisions_by_date(
        dataset,
        mask,
        bundle,
        {"specialists": {0: object(), 1: object()}},
        model.VetoConfig(-0.02, None, False, 0.0),
        calibrated_dispersion_thresholds={0.75: 0.1, 0.90: 0.2},
    )[stamp]
    assert result["selected"] == [1]
    assert set(result["selected"]).issubset(set(baseline[stamp]["selected"]))
    assert result["vetoed_assets"] == ["BTC"]


def fold_result(
    baseline_return: float,
    veto_return: float,
    *,
    baseline_actions: int = 2,
    veto_actions: int = 2,
    baseline_turnover: float = 0.2,
    veto_turnover: float = 0.2,
    baseline_drawdown: float = 0.01,
    veto_drawdown: float = 0.01,
) -> dict:
    return {
        "baseline": {
            "net_return": baseline_return,
            "target_changing_actions": baseline_actions,
            "turnover": baseline_turnover,
            "maximum_drawdown": baseline_drawdown,
        },
        "veto": {
            "net_return": veto_return,
            "target_changing_actions": veto_actions,
            "turnover": veto_turnover,
            "maximum_drawdown": veto_drawdown,
            "veto_reason_counts": {},
        },
    }


def test_eligibility_requires_nonnegative_excess_on_every_fold():
    config = model.VetoConfig(-0.02, None, False, 0.0)
    values = [
        fold_result(0.01, 0.02),
        fold_result(0.01, 0.009),
    ]
    eligible, reasons = model.config_eligibility(values, config)
    assert eligible is False
    assert "negative_minimum_fold_excess" in reasons


def test_eligibility_rejects_more_actions_or_turnover():
    config = model.VetoConfig(-0.02, None, False, 0.0)
    values = [
        fold_result(
            0.01,
            0.011,
            baseline_actions=2,
            veto_actions=3,
            baseline_turnover=0.2,
            veto_turnover=0.3,
        )
    ]
    eligible, reasons = model.config_eligibility(values, config)
    assert eligible is False
    assert "increased_actions" in reasons
    assert "increased_turnover" in reasons


def test_disabled_baseline_is_always_eligible():
    values = [fold_result(0.01, -0.50, veto_actions=99, veto_turnover=99.0)]
    assert model.config_eligibility(values, model.DISABLED_VETO) == (True, [])


def test_veto_grid_contains_single_disabled_baseline():
    grid = model.veto_grid()
    assert grid.count(model.DISABLED_VETO) == 1
    assert grid[0] == model.DISABLED_VETO


def test_source_keeps_paper_only_boundary():
    text = Path(model.__file__).read_text(encoding="utf-8").lower()
    assert "private_key" not in text
    assert "create_order" not in text
    assert "place_order" not in text
    assert '"authorizes_trading": false' in text

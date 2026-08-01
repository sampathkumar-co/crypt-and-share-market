from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tradebot.research import cross_exchange_target_ensemble_v49 as model
from tradebot.research.regime_ranking_v42 import Dataset


def manual_dataset(
    start: datetime,
    day_count: int,
    *,
    feature_count: int,
) -> Dataset:
    dates: list[datetime] = []
    assets: list[str] = []
    returns: list[float] = []
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for asset in model.ASSETS:
            dates.append(stamp)
            assets.append(asset)
            returns.append(
                0.01 if asset == "BTC"
                else 0.005 if asset == "ETH"
                else 0.0
            )
    size = len(dates)
    return Dataset(
        X=np.zeros((size, feature_count), dtype=float),
        return1=np.asarray(returns),
        return3=np.zeros(size),
        return7=np.zeros(size),
        rank3=np.zeros(size),
        meta=np.ones(size, dtype=int),
        downside3=np.zeros(size, dtype=int),
        regimes=np.zeros(size, dtype=int),
        dates=dates,
        assets=assets,
        feature_names=[f"x{index}" for index in range(feature_count)],
    )


def cash_history(start: datetime) -> model.v44.CashRateHistory:
    rates = {start - timedelta(days=1): 0.05}
    return model.v44.CashRateHistory(
        annual_rates=rates,
        source={
            "provider": model.v44.CASH_PROVIDER,
            "series": model.v44.CASH_SERIES,
            "observation_count": 1,
            "first_date": min(rates).date().isoformat(),
            "last_date": max(rates).date().isoformat(),
        },
    )


def decisions(
    dataset: Dataset,
    mask: np.ndarray,
    *,
    selected_asset: str,
    panic_offset: int | None = None,
):
    stamps = sorted({
        dataset.dates[index]
        for index in np.flatnonzero(mask)
    })
    result = {}
    for offset, stamp in enumerate(stamps):
        indexes = [
            index
            for index in np.flatnonzero(mask)
            if dataset.dates[index] == stamp
        ]
        selected = next(
            index
            for index in indexes
            if dataset.assets[index] == selected_asset
        )
        panic = offset == panic_offset
        result[stamp] = {
            "regime": 2 if panic else 0,
            "selected": [] if panic else [selected],
            "candidate_count": 0 if panic else 1,
            "panic_probability": 1.0 if panic else 0.0,
        }
    return result


def summary(
    net_return: float,
    *,
    turnover: float = 1.0,
    drawdown: float = 0.01,
) -> dict[str, float]:
    return {
        "net_return": net_return,
        "turnover": turnover,
        "maximum_drawdown": drawdown,
        "maximum_target_exposure": 0.05,
    }


def fold_result(
    ensemble_return: float,
    control_return: float = 0.01,
    *,
    ensemble_turnover: float = 1.0,
    control_turnover: float = 1.0,
) -> dict[str, object]:
    return {
        "ensemble_standard": summary(
            ensemble_return,
            turnover=ensemble_turnover,
        ),
        "ensemble_stress": summary(
            ensemble_return - 0.001,
            turnover=ensemble_turnover,
        ),
        "control_standard": summary(
            control_return,
            turnover=control_turnover,
        ),
        "control_stress": summary(
            control_return - 0.001,
            turnover=control_turnover,
        ),
    }


def test_candidate_weights_are_fixed_and_bounded():
    configs = model.candidate_configs()
    assert [value.combined_weight for value in configs] == [
        0.25, 0.50, 0.75
    ]
    assert all(
        value.control_weight + value.combined_weight
        == pytest.approx(1.0)
        for value in configs
    )


def test_same_asset_target_conserves_five_percent():
    components = model._target_components(
        1.0,
        ("BTC",),
        ("BTC",),
        model.EnsembleConfig(0.25),
    )
    totals = model._totals(components)
    assert totals["BTC"] == pytest.approx(0.05)
    assert sum(totals.values()) == pytest.approx(0.05)
    assert components["control"]["BTC"] == pytest.approx(0.0375)
    assert components["combined"]["BTC"] == pytest.approx(0.0125)


def test_different_assets_split_capital_without_extra_exposure():
    components = model._target_components(
        1.0,
        ("BTC",),
        ("ETH",),
        model.EnsembleConfig(0.25),
    )
    totals = model._totals(components)
    assert totals["BTC"] == pytest.approx(0.0375)
    assert totals["ETH"] == pytest.approx(0.0125)
    assert sum(totals.values()) == pytest.approx(0.05)


def test_two_asset_sleeves_never_exceed_ten_percent():
    components = model._target_components(
        1.0,
        ("BTC", "ETH"),
        ("SOL", "XRP"),
        model.EnsembleConfig(0.50),
    )
    assert sum(model._totals(components).values()) == pytest.approx(0.10)


def test_zero_combined_weight_exactly_reproduces_control(monkeypatch):
    start = model.v43.day("2025-01-02")
    base = manual_dataset(start, 9, feature_count=2)
    combined = manual_dataset(start, 9, feature_count=3)
    mask = np.ones(len(base.X), dtype=bool)
    monkeypatch.setattr(
        model.v43,
        "predict_components",
        lambda *_args, **_kwargs: {},
    )

    def fixed(dataset, mask, *_args):
        asset = "BTC" if len(dataset.feature_names) == 2 else "ETH"
        return decisions(dataset, mask, selected_asset=asset)

    monkeypatch.setattr(model.v43, "decisions_by_date", fixed)
    history = cash_history(start)
    baseline = model.v44.simulate(
        base,
        mask,
        object(),
        {},
        history,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    ensemble = model.simulate_ensemble(
        base,
        combined,
        mask,
        object(),
        object(),
        history,
        model.EnsembleConfig(0.0),
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert ensemble["net_return"] == pytest.approx(
        baseline["net_return"]
    )
    assert ensemble["turnover"] == pytest.approx(
        baseline["turnover"]
    )
    assert ensemble["target_changing_actions"] == baseline[
        "target_changing_actions"
    ]
    assert ensemble["selected_assets"] == baseline["selected_assets"]


def test_combined_panic_cashes_only_combined_sleeve(monkeypatch):
    start = model.v43.day("2025-01-02")
    base = manual_dataset(start, 6, feature_count=2)
    combined = manual_dataset(start, 6, feature_count=3)
    mask = np.ones(len(base.X), dtype=bool)
    monkeypatch.setattr(
        model.v43,
        "predict_components",
        lambda *_args, **_kwargs: {},
    )

    def fixed(dataset, mask, *_args):
        if len(dataset.feature_names) == 2:
            return decisions(dataset, mask, selected_asset="BTC")
        return decisions(
            dataset,
            mask,
            selected_asset="ETH",
            panic_offset=1,
        )

    monkeypatch.setattr(model.v43, "decisions_by_date", fixed)
    ensemble = model.simulate_ensemble(
        base,
        combined,
        mask,
        object(),
        object(),
        cash_history(start),
        model.EnsembleConfig(0.25),
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert ensemble["agreement_counts"]["combined_panic"] == 1
    assert ensemble["maximum_target_exposure"] <= 0.0500001
    assert "BTC" in ensemble["selected_assets"]


def test_one_sleeve_panic_does_not_delay_other_sleeve_cadence(monkeypatch):
    start = model.v43.day("2025-01-02")
    base = manual_dataset(start, 4, feature_count=2)
    combined = manual_dataset(start, 4, feature_count=3)
    mask = np.ones(len(base.X), dtype=bool)
    monkeypatch.setattr(
        model.v43,
        "predict_components",
        lambda *_args, **_kwargs: {},
    )

    def independent(dataset, mask, *_args):
        stamps = sorted({
            dataset.dates[index]
            for index in np.flatnonzero(mask)
        })
        result = {}
        for offset, stamp in enumerate(stamps):
            indexes = [
                index
                for index in np.flatnonzero(mask)
                if dataset.dates[index] == stamp
            ]
            is_control = len(dataset.feature_names) == 2
            panic = not is_control and offset == 1
            asset = (
                "ETH"
                if is_control and offset == 3
                else "BTC"
                if is_control
                else "SOL"
            )
            selected = next(
                index
                for index in indexes
                if dataset.assets[index] == asset
            )
            result[stamp] = {
                "regime": 2 if panic else 0,
                "selected": [] if panic else [selected],
                "candidate_count": 0 if panic else 1,
                "panic_probability": 1.0 if panic else 0.0,
            }
        return result

    monkeypatch.setattr(model.v43, "decisions_by_date", independent)
    ensemble = model.simulate_ensemble(
        base,
        combined,
        mask,
        object(),
        object(),
        cash_history(start),
        model.EnsembleConfig(0.25),
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert ensemble["agreement_counts"]["combined_panic"] == 1
    assert ensemble["sleeve_due_counts"]["control"] == 2
    assert "ETH" in ensemble["decision_selected_assets"]


def test_active_eligibility_accepts_stable_positive_ensemble():
    folds = [
        fold_result(0.012 if index < 4 else 0.009)
        for index in range(6)
    ]
    eligible, reasons = model.active_eligibility(folds)
    assert eligible is True
    assert reasons == []


def test_active_eligibility_rejects_excess_turnover():
    folds = [
        fold_result(
            0.012,
            ensemble_turnover=2.0,
            control_turnover=1.0,
        )
        for _ in range(6)
    ]
    eligible, reasons = model.active_eligibility(folds)
    assert eligible is False
    assert "aggregate_turnover_exceeded" in reasons
    assert "fold_turnover_exceeded" in reasons


def test_selection_key_prefers_lower_weight_after_equal_results():
    folds = [fold_result(0.012) for _ in range(6)]
    low = model._selection_key(
        folds,
        model.EnsembleConfig(0.25),
    )
    high = model._selection_key(
        folds,
        model.EnsembleConfig(0.75),
    )
    assert low > high

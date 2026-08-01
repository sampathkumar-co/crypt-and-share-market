from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tradebot.research import dollar_rates_attenuation_v48 as model
from tradebot.research.regime_ranking_v42 import Dataset


def day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def manual_dataset(start: datetime, day_count: int) -> Dataset:
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


def cash_history(start: datetime) -> model.v44.CashRateHistory:
    return model.v44.CashRateHistory(
        annual_rates={start: 0.05},
        source={"series": "DGS3MO"},
    )


def fixed_decisions(dataset: Dataset, mask: np.ndarray, *_args):
    result = {}
    for stamp in sorted({
        dataset.dates[index] for index in np.flatnonzero(mask)
    }):
        indexes = [
            index
            for index in np.flatnonzero(mask)
            if dataset.dates[index] == stamp
        ]
        btc = next(
            index for index in indexes if dataset.assets[index] == "BTC"
        )
        result[stamp] = {
            "regime": 0,
            "selected": [btc],
            "candidate_count": 1,
            "panic_probability": 0.0,
        }
    return result


def summary(
    net_return: float,
    *,
    attenuated: int = 0,
    actions: int = 1,
    turnover: float = 0.1,
    drawdown: float = 0.01,
) -> dict:
    return {
        "net_return": net_return,
        "maximum_drawdown": drawdown,
        "turnover": turnover,
        "target_changing_actions": actions,
        "selected_assets": ["BTC"],
        "attenuated_assets": ["BTC"] if attenuated else [],
        "attenuated_decision_count": attenuated,
        "minimum_applied_multiplier": 0.5 if attenuated else 1.0,
        "maximum_applied_multiplier": 1.0,
        "asset_contribution": {asset: 0.0 for asset in model.ASSETS},
        "regime_contribution": {
            name: 0.0 for name in model.REGIME_NAMES.values()
        },
        "cash_contribution": 0.0,
        "decision_count": 1,
        "maximum_gross_exposure": 0.05,
        "maximum_target_exposure": 0.05,
        "maximum_selected_cardinality": 1,
        "never_added_asset": True,
        "never_increased_target": True,
    }


def fold_result(
    index: int,
    multiplier: float,
    excess: float,
    *,
    attenuated: int = 1,
) -> model.AttenuationFoldResult:
    baseline = summary(0.01)
    gated = summary(0.01 + excess, attenuated=attenuated)
    return model.AttenuationFoldResult(
        fold=f"WF-{index + 1}",
        multiplier=multiplier,
        threshold=0.5,
        training_date_count=200,
        positive_label_share=0.5,
        calibration_months=[],
        calibration_minimum_excess=0.0,
        calibration_compounded_excess=0.0,
        validation_baseline=baseline,
        validation_attenuated=gated,
        validation_excess=excess,
    )


def test_calendar_month_blocks_cover_quarter_without_overlap():
    blocks = model.calendar_month_blocks(
        day("2025-07-01"),
        day("2025-09-30"),
    )
    assert [name for name, _, _ in blocks] == [
        "2025-07", "2025-08", "2025-09"
    ]
    assert blocks[0][1:] == (day("2025-07-01"), day("2025-07-31"))
    assert blocks[-1][1:] == (day("2025-09-01"), day("2025-09-30"))


def test_attenuation_decisions_reduce_only_below_threshold(monkeypatch):
    dataset = manual_dataset(day("2025-01-02"), 2)
    mask = np.ones(len(dataset.X), dtype=bool)
    monkeypatch.setattr(model.v43, "decisions_by_date", fixed_decisions)
    decisions = model.attenuation_decisions(
        dataset,
        mask,
        object(),
        {},
        {
            day("2025-01-02"): 0.4,
            day("2025-01-03"): 0.7,
        },
        0.5,
        0.5,
    )
    assert decisions[day("2025-01-02")]["target_multiplier"] == 0.5
    assert decisions[day("2025-01-03")]["target_multiplier"] == 1.0


def test_attenuation_rejects_multiplier_above_one(monkeypatch):
    dataset = manual_dataset(day("2025-01-02"), 1)
    mask = np.ones(len(dataset.X), dtype=bool)
    monkeypatch.setattr(model.v43, "decisions_by_date", fixed_decisions)
    with pytest.raises(
        model.DollarRatesAttenuationV48Error,
        match="outside",
    ):
        model.attenuation_decisions(
            dataset,
            mask,
            object(),
            {},
            {day("2025-01-02"): 0.4},
            0.5,
            1.1,
        )


def test_simulation_never_increases_baseline_target(monkeypatch):
    dataset = manual_dataset(day("2025-01-02"), 6)
    mask = np.ones(len(dataset.X), dtype=bool)
    monkeypatch.setattr(model.v43, "decisions_by_date", fixed_decisions)
    result = model.simulate_attenuation(
        dataset,
        mask,
        object(),
        {},
        cash_history(day("2025-01-01")),
        {stamp: 0.4 for stamp in set(dataset.dates)},
        0.5,
        0.5,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert result["attenuated_decision_count"] > 0
    assert result["minimum_applied_multiplier"] == pytest.approx(0.5)
    assert result["maximum_target_exposure"] <= 0.0250001
    assert result["maximum_selected_cardinality"] == 1
    assert result["never_added_asset"] is True
    assert result["never_increased_target"] is True


def test_normalized_baseline_adds_zero_attenuation_fields():
    baseline = summary(0.01)
    for key in (
        "attenuated_assets",
        "attenuated_decision_count",
        "minimum_applied_multiplier",
        "maximum_applied_multiplier",
        "maximum_selected_cardinality",
        "never_added_asset",
        "never_increased_target",
    ):
        baseline.pop(key)
    normalized = model.normalized_baseline(baseline)
    assert normalized["attenuated_decision_count"] == 0
    assert normalized["minimum_applied_multiplier"] == 1.0
    assert normalized["never_increased_target"] is True
    assert normalized["net_return"] == baseline["net_return"]


def test_selection_uses_disabled_fallback_when_active_is_inconsistent():
    active = [
        fold_result(index, 0.5, 0.001 if index == 0 else 0.0)
        for index in range(6)
    ]
    selected, report = model.select_multiplier({0.5: active})
    assert selected == 1.0
    assert report["selected_is_disabled_baseline"] is True


def test_selection_can_choose_robust_active_multiplier():
    active = [
        fold_result(index, 0.75, 0.001, attenuated=2)
        for index in range(6)
    ]
    selected, report = model.select_multiplier({0.75: active})
    assert selected == 0.75
    assert report["selected_is_disabled_baseline"] is False
    candidate = next(
        value for value in report["candidates"]
        if value["multiplier"] == 0.75
    )
    assert candidate["eligible"] is True
    assert candidate["positive_excess_fold_count"] == 6


def test_active_grids_are_fixed_and_downside_only():
    assert model.ATTENUATION_MULTIPLIERS == (0.25, 0.50, 0.75)
    assert None not in model.ACTIVE_THRESHOLDS
    assert all(0.0 < value < 1.0 for value in model.ATTENUATION_MULTIPLIERS)
    assert model.FAMILY == "dollar_rates"

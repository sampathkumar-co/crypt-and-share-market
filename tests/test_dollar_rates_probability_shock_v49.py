from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tradebot.research import dollar_rates_probability_shock_v49 as model


def day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def summary(
    net_return: float,
    *,
    attenuated: int = 1,
    actions: int = 1,
    turnover: float = 0.1,
    drawdown: float = 0.01,
) -> dict[str, object]:
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
        "maximum_target_exposure": 0.05,
        "maximum_selected_cardinality": 1,
        "never_added_asset": True,
        "never_increased_target": True,
    }


def fold_result(
    index: int,
    shock_family: str,
    excess: float,
    *,
    attenuated: int = 1,
) -> model.ShockFoldResult:
    baseline = summary(0.01, attenuated=0)
    shocked = summary(0.01 + excess, attenuated=attenuated)
    return model.ShockFoldResult(
        fold=f"WF-{index + 1}",
        shock_family=shock_family,
        threshold=0.05,
        training_date_count=200,
        positive_label_share=0.5,
        calibration_months=[],
        calibration_minimum_excess=0.0,
        calibration_compounded_excess=0.0,
        validation_baseline=baseline,
        validation_shocked=shocked,
        validation_excess=excess,
    )


def test_drop_5_uses_exact_past_date():
    probabilities = {
        day("2025-01-01"): 0.8,
        day("2025-01-06"): 0.5,
    }
    shocks = model.probability_shock_by_date(probabilities, "drop_5")
    assert shocks[day("2025-01-06")] == pytest.approx(0.3)


def test_drop_5_uses_newest_earlier_missing_date():
    probabilities = {
        day("2025-01-01"): 0.8,
        day("2025-01-03"): 0.7,
        day("2025-01-09"): 0.4,
    }
    shocks = model.probability_shock_by_date(probabilities, "drop_5")
    assert shocks[day("2025-01-09")] == pytest.approx(0.3)


def test_probability_improvement_clips_to_zero():
    probabilities = {
        day("2025-01-01"): 0.3,
        day("2025-01-21"): 0.6,
    }
    shocks = model.probability_shock_by_date(probabilities, "drop_20")
    assert shocks[day("2025-01-21")] == 0.0


def test_drawdown_20_excludes_current_probability():
    probabilities = {
        day("2025-01-01"): 0.8,
        day("2025-01-10"): 0.7,
        day("2025-01-15"): 0.4,
    }
    shocks = model.probability_shock_by_date(probabilities, "drawdown_20")
    assert shocks[day("2025-01-15")] == pytest.approx(0.4)


def test_future_probability_cannot_change_earlier_shock():
    base = {
        day("2025-01-01"): 0.8,
        day("2025-01-06"): 0.5,
    }
    original = model.probability_shock_by_date(base, "drop_5")
    changed = model.probability_shock_by_date(
        {**base, day("2025-01-20"): 0.0},
        "drop_5",
    )
    assert changed[day("2025-01-06")] == original[day("2025-01-06")]


def test_unknown_shock_family_is_rejected():
    with pytest.raises(model.DollarRatesProbabilityShockV49Error):
        model.probability_shock_by_date({}, "unknown")


def test_simulate_shock_maps_threshold_causally(monkeypatch):
    captured: dict[str, object] = {}

    def fake_simulate(
        _dataset,
        _mask,
        _bundle,
        _predictions,
        _cash,
        probabilities,
        threshold,
        multiplier,
        *,
        one_way_cost,
    ):
        captured.update({
            "probabilities": probabilities,
            "threshold": threshold,
            "multiplier": multiplier,
            "cost": one_way_cost,
        })
        return summary(0.01, attenuated=1)

    monkeypatch.setattr(model.v48, "simulate_attenuation", fake_simulate)
    stamp = day("2025-01-06")

    class Dummy:
        dates = [stamp]

    result = model.simulate_shock(
        Dummy(),
        np.asarray([True]),
        object(),
        {},
        object(),
        {stamp: 0.05},
        "drop_5",
        0.05,
        one_way_cost=0.001,
    )
    assert float(captured["probabilities"][stamp]) < -0.05
    assert captured["threshold"] == pytest.approx(-0.05)
    assert captured["multiplier"] == pytest.approx(0.5)
    assert result["threshold_crossing_date_count"] == 1


def test_robust_active_family_beats_disabled_fallback():
    active = [
        fold_result(index, "drop_5", 0.001, attenuated=2)
        for index in range(6)
    ]
    selected, report = model.select_family({"drop_5": active})
    assert selected == "drop_5"
    assert report["selected_is_disabled_baseline"] is False
    assert report["selected_key"] is not None


def test_inconsistent_family_falls_back_to_disabled():
    active = [
        fold_result(
            index,
            "drop_20",
            0.001 if index < 2 else 0.0,
            attenuated=1,
        )
        for index in range(6)
    ]
    selected, report = model.select_family({"drop_20": active})
    assert selected == model.DISABLED_FAMILY
    assert report["selected_is_disabled_baseline"] is True
    assert report["selected_key"] is None


def test_fold_deficit_below_allowance_is_rejected():
    excesses = [-0.003, 0.002, 0.002, 0.002, 0.002, 0.002]
    active = [
        fold_result(index, "drawdown_20", excess)
        for index, excess in enumerate(excesses)
    ]
    eligible, reasons = model.family_eligibility("drawdown_20", active)
    assert eligible is False
    assert "minimum_fold_excess_below_allowance" in reasons


def test_protocol_grids_are_fixed_and_downside_only():
    assert model.SHOCK_FAMILIES == ("drop_5", "drop_20", "drawdown_20")
    assert model.SHOCK_THRESHOLDS == (0.025, 0.05, 0.075, 0.10, 0.15, 0.20)
    assert model.ACTIVE_MULTIPLIER == pytest.approx(0.5)
    assert model.FAMILY == "dollar_rates"

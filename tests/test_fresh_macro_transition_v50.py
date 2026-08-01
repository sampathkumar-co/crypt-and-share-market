from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from tradebot.research import fresh_macro_transition_v50 as model


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
    family: str,
    excess: float,
    *,
    attenuated: int = 1,
) -> model.TransitionFoldResult:
    baseline = summary(0.01, attenuated=0)
    transition = summary(0.01 + excess, attenuated=attenuated)
    return model.TransitionFoldResult(
        fold=f"WF-{index + 1}",
        transition_family=family,
        threshold=0.5,
        training_date_count=200,
        positive_label_share=0.5,
        calibration_months=[],
        calibration_minimum_excess=0.0,
        calibration_compounded_excess=0.0,
        validation_baseline=baseline,
        validation_transition=transition,
        validation_excess=excess,
    )


def test_first_low_date_does_not_create_crossing():
    active, crossings = model.transition_active_by_date(
        {day("2025-01-01"): 0.4},
        0.5,
        7,
    )
    assert crossings[day("2025-01-01")] is False
    assert active[day("2025-01-01")] is False


def test_downward_crossing_starts_window():
    active, crossings = model.transition_active_by_date(
        {
            day("2025-01-01"): 0.6,
            day("2025-01-02"): 0.4,
        },
        0.5,
        3,
    )
    assert crossings[day("2025-01-02")] is True
    assert active[day("2025-01-02")] is True


def test_three_day_window_has_exact_calendar_boundary():
    probabilities = {
        day("2025-01-01"): 0.6,
        day("2025-01-02"): 0.4,
        day("2025-01-03"): 0.4,
        day("2025-01-04"): 0.4,
        day("2025-01-05"): 0.4,
    }
    active, _ = model.transition_active_by_date(
        probabilities,
        0.5,
        3,
    )
    assert active[day("2025-01-02")] is True
    assert active[day("2025-01-03")] is True
    assert active[day("2025-01-04")] is True
    assert active[day("2025-01-05")] is False


def test_persistent_low_state_does_not_restart_window():
    probabilities = {
        day("2025-01-01"): 0.6,
        day("2025-01-02"): 0.4,
        day("2025-01-10"): 0.3,
    }
    active, crossings = model.transition_active_by_date(
        probabilities,
        0.5,
        3,
    )
    assert crossings[day("2025-01-10")] is False
    assert active[day("2025-01-10")] is False


def test_recovery_and_recrossing_rearm_controller():
    probabilities = {
        day("2025-01-01"): 0.6,
        day("2025-01-02"): 0.4,
        day("2025-01-10"): 0.6,
        day("2025-01-11"): 0.4,
    }
    active, crossings = model.transition_active_by_date(
        probabilities,
        0.5,
        3,
    )
    assert crossings[day("2025-01-11")] is True
    assert active[day("2025-01-11")] is True


def test_future_probability_cannot_change_earlier_activity():
    base = {
        day("2025-01-01"): 0.6,
        day("2025-01-02"): 0.4,
    }
    original, _ = model.transition_active_by_date(base, 0.5, 7)
    changed, _ = model.transition_active_by_date(
        {**base, day("2025-02-01"): 0.1},
        0.5,
        7,
    )
    assert changed[day("2025-01-02")] == original[day("2025-01-02")]


def test_invalid_transition_arguments_are_rejected():
    with pytest.raises(model.FreshMacroTransitionV50Error):
        model.transition_active_by_date({}, 1.0, 7)
    with pytest.raises(model.FreshMacroTransitionV50Error):
        model.transition_active_by_date({}, 0.5, 0)


def test_simulation_adapter_maps_active_state(monkeypatch):
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
    stamp = day("2025-01-02")

    class Dummy:
        dates = [stamp]

    result = model.simulate_transition(
        Dummy(),
        np.asarray([True]),
        object(),
        {},
        object(),
        {stamp: True},
        {stamp: True},
        "fresh_3d",
        0.5,
        one_way_cost=0.001,
    )
    assert captured["probabilities"][stamp] == 0.0
    assert captured["threshold"] == pytest.approx(0.5)
    assert captured["multiplier"] == pytest.approx(0.5)
    assert result["crossing_count"] == 1
    assert result["active_transition_date_count"] == 1


def test_robust_transition_family_beats_disabled():
    active = [
        fold_result(index, "fresh_3d", 0.001, attenuated=2)
        for index in range(6)
    ]
    selected, report = model.select_family({"fresh_3d": active})
    assert selected == "fresh_3d"
    assert report["selected_is_disabled_baseline"] is False
    assert report["selected_key"] is not None


def test_inconsistent_transition_family_falls_back():
    active = [
        fold_result(
            index,
            "fresh_7d",
            0.001 if index < 2 else 0.0,
            attenuated=1,
        )
        for index in range(6)
    ]
    selected, report = model.select_family({"fresh_7d": active})
    assert selected == model.DISABLED_FAMILY
    assert report["selected_is_disabled_baseline"] is True
    assert report["selected_key"] is None


def test_shorter_window_wins_exact_tie():
    short = [
        fold_result(index, "fresh_3d", 0.001, attenuated=1)
        for index in range(6)
    ]
    long = [
        fold_result(index, "fresh_14d", 0.001, attenuated=1)
        for index in range(6)
    ]
    selected, _ = model.select_family({
        "fresh_3d": short,
        "fresh_14d": long,
    })
    assert selected == "fresh_3d"


def test_protocol_grids_are_fixed():
    assert model.TRANSITION_WINDOWS == {
        "fresh_3d": 3,
        "fresh_7d": 7,
        "fresh_14d": 14,
    }
    assert model.STATE_THRESHOLDS == (
        0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65
    )
    assert model.ACTIVE_MULTIPLIER == pytest.approx(0.5)
    assert model.FAMILY == "dollar_rates"

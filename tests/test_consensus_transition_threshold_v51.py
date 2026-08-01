from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradebot.research import consensus_transition_threshold_v51 as model


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
    excess: float,
    *,
    attenuated: int = 1,
) -> model.FixedThresholdFoldResult:
    baseline = summary(0.01, attenuated=0)
    transition = summary(0.01 + excess, attenuated=attenuated)
    return model.FixedThresholdFoldResult(
        fold=f"WF-{index + 1}",
        threshold=0.6,
        training_date_count=200,
        positive_label_share=0.5,
        validation_baseline=baseline,
        validation_transition=transition,
        validation_excess=excess,
    )


def test_exact_grid_median_is_preserved():
    result = model.consensus_threshold(
        [0.60, 0.55, 0.65, 0.65, 0.60, 0.50]
    )
    assert result["raw_median"] == pytest.approx(0.60)
    assert result["consensus_threshold"] == pytest.approx(0.60)
    assert result["sorted_thresholds"] == [
        0.50, 0.55, 0.60, 0.60, 0.65, 0.65
    ]


def test_between_grid_median_rounds_upward():
    result = model.consensus_threshold(
        [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    )
    assert result["raw_median"] == pytest.approx(0.475)
    assert result["consensus_threshold"] == pytest.approx(0.50)


def test_consensus_requires_exactly_six_thresholds():
    with pytest.raises(model.ConsensusTransitionThresholdV51Error):
        model.consensus_threshold([0.50, 0.55])


def test_consensus_rejects_off_grid_input():
    with pytest.raises(model.ConsensusTransitionThresholdV51Error):
        model.consensus_threshold(
            [0.50, 0.55, 0.60, 0.60, 0.625, 0.65]
        )


def test_consensus_is_order_independent():
    forward = model.consensus_threshold(
        [0.60, 0.55, 0.65, 0.65, 0.60, 0.50]
    )
    reverse = model.consensus_threshold(
        [0.50, 0.60, 0.65, 0.65, 0.55, 0.60]
    )
    assert reverse["consensus_threshold"] == forward[
        "consensus_threshold"
    ]
    assert reverse["sorted_thresholds"] == forward[
        "sorted_thresholds"
    ]


def test_fixed_threshold_audit_accepts_robust_results():
    results = [
        fold_result(index, 0.001, attenuated=1)
        for index in range(6)
    ]
    audit = model.fixed_threshold_audit(results)
    assert audit["eligible"] is True
    assert audit["ineligibility_reasons"] == []
    assert audit["positive_excess_fold_count"] == 6


def test_fixed_threshold_audit_rejects_inconsistent_results():
    results = [
        fold_result(
            index,
            0.001 if index < 2 else 0.0,
            attenuated=1,
        )
        for index in range(6)
    ]
    audit = model.fixed_threshold_audit(results)
    assert audit["eligible"] is False
    assert "fewer_than_four_positive_excess_folds" in audit[
        "ineligibility_reasons"
    ]


def test_fixed_threshold_audit_rejects_large_fold_loss():
    excesses = [-0.003, 0.002, 0.002, 0.002, 0.002, 0.002]
    audit = model.fixed_threshold_audit([
        fold_result(index, excess)
        for index, excess in enumerate(excesses)
    ])
    assert audit["eligible"] is False
    assert "minimum_fold_excess_below_allowance" in audit[
        "ineligibility_reasons"
    ]


def test_disabled_final_states_are_zero_valued():
    class Dummy:
        dates = [day("2025-01-01"), day("2025-01-02")]

    active, crossings, audit = model.disabled_final_states(Dummy())
    assert set(active.values()) == {False}
    assert set(crossings.values()) == {False}
    assert audit["transition_family"] == model.v50.DISABLED_FAMILY
    assert audit["threshold"] is None
    assert audit["used_for_selection"] is False


def test_frozen_v50_inputs_are_explicit():
    assert model.FAMILY == "fresh_14d"
    assert model.WINDOW_DAYS == 14
    assert model.ACTIVE_MULTIPLIER == pytest.approx(0.5)
    assert model.EXPECTED_V50_THRESHOLDS == (
        0.60, 0.55, 0.65, 0.65, 0.60, 0.50
    )

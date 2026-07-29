from __future__ import annotations

import pytest

from tradebot.backtest.meta_allocation import (
    MetaAllocationConfig,
    correlation_filter,
    exposure_from_confidence,
    next_drawdown_multiplier,
    parameter_distance,
    select_parameter_plateau,
)
from tradebot.backtest.research_gate import EXECUTION_PROFILES


def candidate(
    lookback: int,
    min_return: float,
    volume_multiplier: float,
    execution_index: int,
    score: float,
    positive_fold_fraction: float = 0.80,
):
    return {
        "strategy_parameters": {
            "lookback": lookback,
            "min_return": min_return,
            "volume_multiplier": volume_multiplier,
        },
        "execution_parameters": dict(EXECUTION_PROFILES[execution_index]),
        "stability_score": score,
        "training_stability": {
            "positive_active_fold_fraction": positive_fold_fraction,
            "stable": True,
        },
    }


def test_parameter_distance_uses_grid_neighbourhood_not_raw_scale():
    base = candidate(3, 0.008, 1.0, 0, 1.0)
    neighbour = candidate(5, 0.008, 1.0, 0, 0.9)
    distant = candidate(8, 0.025, 1.35, 2, 0.8)

    near_distance = parameter_distance(
        "momentum",
        base["strategy_parameters"],
        base["execution_parameters"],
        neighbour["strategy_parameters"],
        neighbour["execution_parameters"],
    )
    far_distance = parameter_distance(
        "momentum",
        base["strategy_parameters"],
        base["execution_parameters"],
        distant["strategy_parameters"],
        distant["execution_parameters"],
    )

    assert near_distance < far_distance
    assert near_distance == pytest.approx(0.125)
    assert far_distance == pytest.approx(1.0)


def test_isolated_stable_peak_is_rejected():
    config = MetaAllocationConfig(
        stability_screen_candidates=3,
        ensemble_candidates=3,
        min_plateau_members=2,
    )
    result = select_parameter_plateau(
        "momentum",
        [candidate(3, 0.008, 1.0, 0, 1.0)],
        config,
    )

    assert not result["ensemble"]
    assert "stable_candidates_are_isolated_parameter_peaks" in result["reasons"]


def test_neighbouring_stable_candidates_form_consensus_ensemble():
    config = MetaAllocationConfig(
        stability_screen_candidates=3,
        ensemble_candidates=3,
        min_plateau_members=2,
        min_consensus_strength=0.50,
    )
    result = select_parameter_plateau(
        "momentum",
        [
            candidate(3, 0.008, 1.0, 0, 1.00),
            candidate(5, 0.008, 1.0, 0, 0.95),
            candidate(8, 0.025, 1.35, 2, 0.60),
        ],
        config,
    )

    assert len(result["plateau"]) == 2
    assert len(result["ensemble"]) == 2
    assert result["consensus_strength"] >= config.min_consensus_strength
    assert not result["reasons"]


def test_confidence_and_volatility_bound_exposure():
    config = MetaAllocationConfig(
        min_consensus_strength=0.50,
        target_annual_volatility=0.20,
        min_total_exposure=0.05,
        max_total_exposure=0.60,
        min_cash_reserve=0.40,
    )

    low_vol = exposure_from_confidence(0.80, 0.10, config)
    high_vol = exposure_from_confidence(0.80, 0.80, config)
    weak = exposure_from_confidence(0.40, 0.10, config)

    assert 0 < high_vol < low_vol <= 0.60
    assert weak == 0.0


def test_correlation_filter_keeps_higher_confidence_sleeve():
    selected, rejected = correlation_filter(
        [
            ("BTCUSDT", 0.90, [0.01, 0.02, -0.01, 0.03]),
            ("ETHUSDT", 0.70, [0.01, 0.02, -0.01, 0.03]),
            ("XRPUSDT", 0.60, [-0.02, 0.01, 0.03, -0.01]),
        ],
        max_pair_correlation=0.80,
    )

    assert "BTCUSDT" in selected
    assert "ETHUSDT" in rejected
    assert "XRPUSDT" in selected


def test_drawdown_brake_applies_then_recovers_gradually():
    config = MetaAllocationConfig(
        drawdown_brake_trigger=0.08,
        drawdown_brake_multiplier=0.50,
        drawdown_recovery_step=0.20,
    )

    braked = next_drawdown_multiplier(1.0, 0.10, config)
    recovered = next_drawdown_multiplier(braked, 0.02, config)

    assert braked == 0.50
    assert recovered == 0.70


def test_exposure_and_cash_reserve_cannot_conflict():
    with pytest.raises(ValueError, match="cash_reserve"):
        MetaAllocationConfig(
            max_total_exposure=0.80,
            min_cash_reserve=0.40,
        )

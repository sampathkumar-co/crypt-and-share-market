from __future__ import annotations

import pytest

from tradebot.backtest.combined_ablation import (
    CombinedAblationConfig,
    alpha_parameter_distance,
    select_alpha_plateau,
)
from tradebot.backtest.research_gate import EXECUTION_PROFILES


def candidate(
    lookback: int,
    short_lookback: int,
    top_n: int,
    min_return: float,
    execution_index: int,
    score: float,
    fold_fraction: float = 0.80,
):
    return {
        "strategy_parameters": {
            "lookback": lookback,
            "short_lookback": short_lookback,
            "top_n": top_n,
            "min_return": min_return,
        },
        "execution_parameters": dict(EXECUTION_PROFILES[execution_index]),
        "stability_score": score,
        "training_stability": {
            "positive_active_fold_fraction": fold_fraction,
            "stable": True,
        },
    }


def test_alpha_distance_uses_committed_grid_coordinates():
    left = candidate(20, 5, 1, 0.0, 0, 1.0)
    neighbour = candidate(40, 5, 1, 0.0, 0, 0.9)
    distant = candidate(60, 10, 2, 0.03, 2, 0.8)

    near = alpha_parameter_distance(
        left["strategy_parameters"],
        left["execution_parameters"],
        neighbour["strategy_parameters"],
        neighbour["execution_parameters"],
    )
    far = alpha_parameter_distance(
        left["strategy_parameters"],
        left["execution_parameters"],
        distant["strategy_parameters"],
        distant["execution_parameters"],
    )

    assert near == pytest.approx(0.10)
    assert far == pytest.approx(1.0)


def test_relative_strength_plateau_rejects_isolated_peak():
    config = CombinedAblationConfig(
        screen_candidates=3,
        ensemble_candidates=3,
        min_plateau_members=2,
    )
    result = select_alpha_plateau(
        [candidate(20, 5, 1, 0.0, 0, 1.0)],
        config,
    )

    assert not result["ensemble"]
    assert "relative_strength_candidates_are_isolated_parameter_peaks" in result["reasons"]


def test_neighbouring_relative_strength_candidates_form_ensemble():
    config = CombinedAblationConfig(
        screen_candidates=3,
        ensemble_candidates=3,
        min_plateau_members=2,
        min_consensus_strength=0.50,
    )
    result = select_alpha_plateau(
        [
            candidate(20, 5, 1, 0.0, 0, 1.00),
            candidate(40, 5, 1, 0.0, 0, 0.95),
            candidate(60, 10, 2, 0.03, 2, 0.50),
        ],
        config,
    )

    assert len(result["plateau"]) == 2
    assert len(result["ensemble"]) == 2
    assert result["consensus_strength"] >= config.min_consensus_strength
    assert not result["reasons"]


def test_combined_config_keeps_required_cash_reserve():
    with pytest.raises(ValueError, match="cash_reserve"):
        CombinedAblationConfig(
            max_total_exposure=0.80,
            min_cash_reserve=0.40,
        )


def test_combined_windows_are_frozen():
    with pytest.raises(ValueError, match="180/60"):
        CombinedAblationConfig(train_size=120, test_size=30)

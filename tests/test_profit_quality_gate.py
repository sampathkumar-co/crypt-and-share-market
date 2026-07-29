from __future__ import annotations

import pytest

from tradebot.backtest.profit_quality_gate import (
    ProfitQualityConfig,
    ProfitQualityPeriod,
    _cash_metrics,
    _summarize_strategy,
)
from tradebot.backtest.selection_stability import (
    TemporalStabilityPolicy,
    stability_adjusted_score,
    stability_reasons,
    summarize_fold_metrics,
    temporal_fold_ranges,
    with_stability_flag,
)


def metrics(net_return: float, trades: int = 2, drawdown: float = 0.03):
    return {
        "net_return": net_return,
        "trades": trades,
        "max_drawdown": drawdown,
    }


def period(
    stable_return: float,
    naive_return: float,
    *,
    abstained: bool = False,
    drawdown: float = 0.05,
) -> ProfitQualityPeriod:
    stable_metrics = metrics(stable_return, drawdown=drawdown)
    naive_metrics = metrics(naive_return, drawdown=max(drawdown, 0.08))
    return ProfitQualityPeriod(
        symbol="TEST",
        strategy="momentum",
        period=1,
        train_start="2025-01-01T00:00:00",
        train_end="2025-06-30T00:00:00",
        unseen_start="2025-07-01T00:00:00",
        unseen_end="2025-08-30T00:00:00",
        stable_abstained=abstained,
        stable_selection_reasons=["unstable"] if abstained else [],
        stable_parameters={} if abstained else {"lookback": 20},
        stable_execution={},
        stable_training_stability={"stable": not abstained},
        naive_parameters={"lookback": 5},
        naive_execution={},
        stable_metrics=stable_metrics,
        naive_metrics=naive_metrics,
        net_return_improvement=stable_return - naive_return,
        drawdown_improvement=float(naive_metrics["max_drawdown"]) - drawdown,
    )


def test_temporal_folds_are_contiguous_and_non_overlapping():
    policy = TemporalStabilityPolicy(fold_count=3, min_fold_bars=30)
    ranges = temporal_fold_ranges(181, policy)

    assert ranges == [(0, 61), (61, 121), (121, 181)]
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))


def test_lucky_single_fold_is_rejected_even_with_positive_full_return():
    policy = TemporalStabilityPolicy(
        min_positive_fold_fraction=0.67,
        min_worst_fold_return=-0.03,
        max_return_dispersion=0.08,
    )
    summary = summarize_fold_metrics(
        [metrics(0.18), metrics(-0.04), metrics(-0.02)]
    )
    reasons = stability_reasons(metrics(0.07, trades=6), summary, policy)

    assert "too_few_positive_training_folds" in reasons
    assert "worst_training_fold_too_negative" in reasons
    assert not with_stability_flag(summary, reasons)["stable"]


def test_stability_score_prefers_consistent_candidate():
    policy = TemporalStabilityPolicy()
    consistent = summarize_fold_metrics(
        [metrics(0.03), metrics(0.025), metrics(0.035)]
    )
    fragile = summarize_fold_metrics(
        [metrics(0.14), metrics(-0.04), metrics(-0.01)]
    )

    stable_score = stability_adjusted_score(0.05, consistent, policy)
    fragile_score = stability_adjusted_score(0.05, fragile, policy)

    assert stable_score > fragile_score
    assert consistent["return_dispersion"] < fragile["return_dispersion"]


def test_profit_quality_summary_approves_real_unseen_improvement():
    config = ProfitQualityConfig(
        max_candidates_per_strategy=4,
        stability_screen_candidates=2,
        min_deployed_periods=2,
        min_positive_deployed_fraction=1.0,
        min_stable_beats_naive_fraction=1.0,
    )
    result = _summarize_strategy(
        "momentum",
        [period(0.04, -0.02), period(0.03, 0.01)],
        config,
    )

    assert result.approved
    assert result.average_stable_return > result.average_naive_return
    assert result.average_net_improvement > 0


def test_cash_abstention_preserves_capital_and_report_is_fail_closed():
    cash = _cash_metrics([], 100000.0)
    config = ProfitQualityConfig(
        max_candidates_per_strategy=2,
        stability_screen_candidates=1,
        min_deployed_periods=1,
    )
    result = _summarize_strategy(
        "momentum",
        [period(0.0, -0.08, abstained=True, drawdown=0.0)],
        config,
    )

    assert cash["ending_cash"] == 100000.0
    assert cash["net_return"] == 0.0
    assert not result.approved
    assert "too_few_stable_deployed_periods" in result.reasons


def test_screening_budget_cannot_exceed_candidate_budget():
    with pytest.raises(ValueError, match="candidate budget"):
        ProfitQualityConfig(
            max_candidates_per_strategy=2,
            stability_screen_candidates=3,
        )

from __future__ import annotations

import math

import pytest

from tradebot.research.intraday_diagnostics_v70 import (
    SourceReplication,
    TradeOutcome,
    build_tournament_diagnostics,
    deflated_sharpe_probability,
    independent_source_replication_passed,
    maximum_drawdown,
    minimum_track_record_length,
    probability_of_backtest_overfitting,
    removal_and_concentration,
)


def outcomes() -> tuple[TradeOutcome, ...]:
    return tuple(
        TradeOutcome(
            trade_id=f"t{index}",
            month_id=f"2026-{1 + index // 10:02d}",
            stress_excess=0.002 + (index % 3) * 0.0002,
            equity_return=0.0015 + (index % 4) * 0.0001,
        )
        for index in range(80)
    )


def test_drawdown_and_concentration_are_computed_from_raw_observations() -> None:
    assert maximum_drawdown((0.10, -0.05, -0.10, 0.20)) == pytest.approx(0.145)
    best_trade_removed, best_month_removed, trade_share, month_share = removal_and_concentration(
        outcomes()
    )
    assert best_trade_removed > 0.0
    assert best_month_removed > 0.0
    assert 0.0 < trade_share < 0.15
    assert 0.0 < month_share <= 0.30


def test_dsr_penalizes_permanent_trial_count() -> None:
    returns = tuple(0.002 + (index % 5) * 0.0002 for index in range(80))
    one_trial = deflated_sharpe_probability(returns, trial_count=1)
    many_trials = deflated_sharpe_probability(returns, trial_count=100)
    assert 0.0 <= many_trials <= one_trial <= 1.0
    assert minimum_track_record_length(returns) <= len(returns)


def test_pbo_detects_fold_selection_instability() -> None:
    stable = (
        (0.04, 0.03, 0.05, 0.04, 0.03, 0.04, 0.05, 0.03),
        (0.01, 0.00, 0.01, 0.00, 0.01, 0.00, 0.01, 0.00),
    )
    unstable = (
        (0.10, 0.10, 0.10, 0.10, -0.10, -0.10, -0.10, -0.10),
        (-0.10, -0.10, -0.10, -0.10, 0.10, 0.10, 0.10, 0.10),
    )
    assert probability_of_backtest_overfitting(stable) == 0.0
    assert probability_of_backtest_overfitting(unstable) > 0.0


def test_independent_replication_fails_closed() -> None:
    positive = (0.002,) * 8
    assert independent_source_replication_passed(
        (
            SourceReplication("binance", positive, 80),
            SourceReplication("coinbase", positive, 80),
        )
    )
    assert not independent_source_replication_passed(
        (
            SourceReplication("binance", positive, 80),
            SourceReplication("coinbase", (-0.002,) * 8, 80),
        )
    )
    assert not independent_source_replication_passed(
        (SourceReplication("binance", positive, 80),)
    )


def test_build_diagnostics_recomputes_every_promotion_input() -> None:
    standard = (0.01,) * 8
    stress = (0.005,) * 8
    delayed = (0.003,) * 8
    matrix = (
        stress,
        (0.001, 0.0, 0.001, 0.0, 0.001, 0.0, 0.001, 0.0),
    )
    replications = (
        SourceReplication("binance", stress, 80),
        SourceReplication("coinbase", stress, 80),
    )
    diagnostics = build_tournament_diagnostics(
        standard,
        stress,
        delayed,
        outcomes(),
        matrix,
        replications,
        trial_count=4,
    )
    assert diagnostics.standard_compounded_excess == pytest.approx((1.01**8) - 1.0)
    assert diagnostics.stress_compounded_excess == pytest.approx((1.005**8) - 1.0)
    assert diagnostics.first_half_excess == pytest.approx((1.01**4) - 1.0)
    assert diagnostics.second_half_excess == pytest.approx((1.01**4) - 1.0)
    assert diagnostics.best_trade_removed_stress_excess > 0.0
    assert diagnostics.best_month_removed_stress_excess > 0.0
    assert diagnostics.minimum_track_record_satisfied
    assert diagnostics.independent_source_replication_passed
    assert math.isfinite(diagnostics.pbo)


def test_invalid_returns_and_misaligned_sources_are_rejected() -> None:
    with pytest.raises(ValueError, match="greater than -100%"):
        maximum_drawdown((-1.0,))
    with pytest.raises(ValueError, match="even number"):
        probability_of_backtest_overfitting(((0.1,) * 9, (0.0,) * 9))
    with pytest.raises(ValueError, match="align"):
        build_tournament_diagnostics(
            (0.01,) * 8,
            (0.005,) * 7,
            (0.003,) * 8,
            outcomes(),
            ((0.01,) * 8, (0.0,) * 8),
            (),
            trial_count=1,
        )

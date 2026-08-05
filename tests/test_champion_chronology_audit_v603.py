from __future__ import annotations

import numpy as np

from tradebot.research import champion_chronology_audit_v603 as v603


def test_annualized_sharpe_fails_closed_on_constant_series():
    assert v603.annualized_sharpe(np.full(200, 0.001)) == 0.0


def test_pbo_detects_stable_candidate_family():
    observations = 240
    base = np.tile(np.asarray([0.0010, 0.0008, -0.0002, 0.0007]), observations // 4)
    strategies = {
        "stable": base,
        "weaker": base - 0.0004,
        "flat": np.zeros(observations),
        "negative": -base,
    }
    result = v603.probability_of_backtest_overfitting(strategies, partitions=8)
    assert result.evaluated_splits == 35
    assert 0.0 <= result.probability_of_backtest_overfitting <= 1.0
    assert result.passed is True


def test_dsr_uses_actual_grid_dispersion():
    rng = np.random.default_rng(7)
    binance = rng.normal(0.0005, 0.005, 1000)
    coinbase = rng.normal(0.00045, 0.005, 1000)
    low_dispersion = v603.deflated_sharpe_audit(
        binance,
        coinbase,
        trial_count=224,
        sharpe_trial_std=0.05,
    )
    high_dispersion = v603.deflated_sharpe_audit(
        binance,
        coinbase,
        trial_count=224,
        sharpe_trial_std=1.0,
    )
    assert low_dispersion.expected_maximum_sharpe < high_dispersion.expected_maximum_sharpe
    assert low_dispersion.probability >= high_dispersion.probability


def test_pbo_rejects_unaligned_series():
    import pytest

    with pytest.raises(v603.ChampionChronologyV603Error):
        v603.probability_of_backtest_overfitting(
            {"a": np.zeros(20), "b": np.zeros(21)},
            partitions=8,
        )

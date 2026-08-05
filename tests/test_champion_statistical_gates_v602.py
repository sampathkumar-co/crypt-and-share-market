from __future__ import annotations

import numpy as np

from tradebot.research import champion_statistical_gates_v602 as v602


def test_positive_constant_series_passes_block_bootstrap():
    values = np.full(240, 0.001, dtype=float)
    result = v602.moving_block_bootstrap(
        values,
        block_length=20,
        resamples=500,
        seed=123,
    )
    assert result.lower_95_bound > 0.0
    assert result.passed is True


def test_bootstrap_is_deterministic_for_frozen_seed():
    values = np.asarray([0.01, -0.005, 0.002, 0.0] * 80, dtype=float)
    left = v602.moving_block_bootstrap(
        values,
        block_length=20,
        resamples=500,
        seed=999,
    )
    right = v602.moving_block_bootstrap(
        values,
        block_length=20,
        resamples=500,
        seed=999,
    )
    assert left == right


def test_expected_maximum_sharpe_penalizes_large_trial_count():
    assert v602.expected_maximum_sharpe(100_000) > 4.0
    assert v602.expected_maximum_sharpe(100_000) > v602.expected_maximum_sharpe(10)


def test_zero_volatility_cannot_pass_dsr_floor():
    values = np.full(500, 0.001, dtype=float)
    result = v602.deflated_sharpe_floor(values)
    assert result.annualized_sharpe == 0.0
    assert result.passed is False


def test_compounded_relative_return_is_geometric():
    values = np.asarray([0.10, -0.05], dtype=float)
    assert abs(v602._compounded(values) - 0.045) < 1e-12

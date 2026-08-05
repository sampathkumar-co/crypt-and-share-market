from __future__ import annotations

import pytest

from tradebot.v5.multiple_testing import (
    ExperimentAttempt,
    ExperimentRegistry,
    deflated_sharpe_probability,
    expected_maximum_sharpe,
    minimum_track_record_length,
    probability_of_backtest_overfitting,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def test_registry_counts_every_attempt_and_is_deterministic() -> None:
    first = ExperimentAttempt("v5-a", "trend", DIGEST_A, "REJECTED", ("2024-Q1",))
    second = ExperimentAttempt("v5-b", "reversal", DIGEST_B, "CANDIDATE", ("2024-Q2",))
    registry = ExperimentRegistry((first,)).append(second)

    assert registry.trial_count == 2
    assert registry.fingerprint == ExperimentRegistry((first, second)).fingerprint


def test_registry_rejects_reused_holdout_intervals() -> None:
    with pytest.raises(ValueError, match="consumed intervals reused"):
        ExperimentRegistry(
            (
                ExperimentAttempt("v5-a", "trend", DIGEST_A, "REJECTED", ("2024-Q1",)),
                ExperimentAttempt("v5-b", "reversal", DIGEST_B, "REJECTED", ("2024-Q1",)),
            )
        )


def test_expected_maximum_sharpe_increases_with_trials() -> None:
    assert expected_maximum_sharpe(1) == 0.0
    assert expected_maximum_sharpe(100) > expected_maximum_sharpe(10) > 0.0


def test_deflated_sharpe_penalizes_more_trials() -> None:
    few_trials = deflated_sharpe_probability(
        0.5, number_of_trials=2, observations=365, sharpe_std=0.25
    )
    many_trials = deflated_sharpe_probability(
        0.5, number_of_trials=1000, observations=365, sharpe_std=0.25
    )
    assert 0.0 <= many_trials < few_trials <= 1.0


def test_minimum_track_record_length_is_stricter_for_weaker_sharpe() -> None:
    strong = minimum_track_record_length(1.5)
    weak = minimum_track_record_length(0.5)
    assert strong < weak
    assert minimum_track_record_length(0.0) == 2**31 - 1


def test_pbo_is_low_for_stable_winner_and_high_for_rotating_winners() -> None:
    stable = {
        "stable": tuple(0.010 + (index % 3) * 0.0001 for index in range(80)),
        "weak": tuple(-0.002 + (index % 5) * 0.0001 for index in range(80)),
        "noise": tuple(((-1) ** index) * 0.004 for index in range(80)),
    }
    rotating = {
        "first_half": tuple(0.02 if index < 40 else -0.02 for index in range(80)),
        "second_half": tuple(-0.02 if index < 40 else 0.02 for index in range(80)),
        "flat": tuple(0.0001 * ((index % 4) - 1.5) for index in range(80)),
    }

    stable_pbo = probability_of_backtest_overfitting(stable, partitions=8)
    rotating_pbo = probability_of_backtest_overfitting(rotating, partitions=8)

    assert stable_pbo <= 0.25
    assert rotating_pbo >= stable_pbo


def test_pbo_rejects_misaligned_series() -> None:
    with pytest.raises(ValueError, match="must align"):
        probability_of_backtest_overfitting({"a": (0.1, 0.2), "b": (0.1,)})

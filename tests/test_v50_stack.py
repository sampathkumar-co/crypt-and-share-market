from datetime import datetime, timedelta, timezone

import pytest

from tradebot.v5.allocation import EngineForecast, allocate
from tradebot.v5.baselines import cross_sectional_trend
from tradebot.v5.costs import CostModel
from tradebot.v5.data_quality import SourceObservation, reconcile_sources, validate_panel
from tradebot.v5.metrics import performance_metrics
from tradebot.v5.regime import RegimeInputs, dominant_regime, regime_probabilities
from tradebot.v5.sealing import SealedPrediction, verify_seal
from tradebot.v5.tournament import (
    CandidateEvidence,
    MultipleTestingEvidence,
    evaluate_candidate,
    rank_candidates,
)


def test_cost_model_stress_and_liquidity_are_conservative():
    model = CostModel()
    standard = model.one_way_bps(liquidity_score=1.0)
    stressed = model.one_way_bps(liquidity_score=0.5, stressed=True)
    assert stressed > standard
    assert model.net_return(0.01, turnover=1.0) < 0.01


def test_metrics_and_drawdown():
    result = performance_metrics([0.01, -0.005, 0.02], periods_per_year=365)
    assert result.compounded_return > 0
    assert 0 < result.maximum_drawdown < 0.01
    assert result.observations == 3


def test_source_reconciliation_is_point_in_time_and_fail_closed():
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observations = [
        SourceObservation("a", timestamp, timestamp + timedelta(minutes=1), 100.0),
        SourceObservation("b", timestamp, timestamp + timedelta(minutes=2), 100.5),
    ]
    with pytest.raises(ValueError, match="insufficient"):
        reconcile_sources(observations, decision_time=timestamp + timedelta(minutes=1))
    point = reconcile_sources(observations, decision_time=timestamp + timedelta(minutes=3))
    assert point.value == 100.25
    with pytest.raises(ValueError, match="disagreement"):
        reconcile_sources(
            observations + [SourceObservation("c", timestamp, timestamp, 120.0)],
            decision_time=timestamp + timedelta(minutes=3),
        )


def test_aligned_panel_required():
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    panel = {
        "BTC": [(first, 1.0), (first + timedelta(days=1), 2.0)],
        "ETH": [(first, 1.0), (first + timedelta(days=1), 1.5)],
    }
    validate_panel(panel)
    with pytest.raises(ValueError, match="not aligned"):
        validate_panel({**panel, "ETH": [(first, 1.0)]})


def test_regime_probabilities_normalize():
    probabilities = regime_probabilities(RegimeInputs(0.1, 0.2, 0.03, 0.8, 0.05, 0.7))
    assert abs(sum(probabilities.values()) - 1.0) < 1e-12
    assert dominant_regime(probabilities) in probabilities


def test_allocator_respects_cash_and_exposure_limits():
    forecast = EngineForecast(0.80, 0.05, 0.05, 1.0)
    decision = allocate(
        {"BTC": {"trend": forecast}, "ETH": {"trend": forecast}, "SOL": {"trend": forecast}},
        {"strong_trend": 0.9, "panic": 0.1},
        {"trend": {"strong_trend": 1.0}},
        expected_cost=0.001,
        disagreement=0.0,
    )
    assert sum(decision.weights.values()) <= 0.10
    assert all(value <= 0.05 for value in decision.weights.values())
    assert decision.cash_weight >= 0.90
    panic = allocate(
        {"BTC": {"trend": forecast}},
        {"panic": 0.9},
        {"trend": {"panic": 1.0}},
        expected_cost=0.0,
        disagreement=0.0,
    )
    assert panic.cash_weight == 1.0


def test_trend_baseline_selects_only_consistent_positive_leaders():
    rising = [float(index + 1) for index in range(100)]
    falling = [float(100 - index) for index in range(100)]
    signal = cross_sectional_trend({"BTC": rising, "ETH": falling, "SOL": [value * 1.1 for value in rising]})
    assert set(signal.weights) == {"BTC", "SOL"}
    assert sum(signal.weights.values()) <= 0.10


def _profitable_candidate(
    candidate_id: str,
    *,
    replicated: bool = True,
    pbo: float = 0.05,
    attempted_configurations: int = 2,
) -> CandidateEvidence:
    pattern = (0.004, 0.003, 0.005, 0.002, 0.004)
    returns = tuple(pattern[index % len(pattern)] for index in range(400))
    stressed = tuple(value - 0.0005 for value in returns)
    return CandidateEvidence(
        candidate_id=candidate_id,
        returns=returns,
        stressed_returns=stressed,
        decisions=40,
        sequential_window_returns=(0.03, 0.02, 0.04, 0.01, 0.03),
        stressed_window_returns=(0.02, 0.01, 0.03, 0.005, 0.02),
        asset_contributions={"BTC": 0.4, "ETH": 0.3, "SOL": 0.3},
        largest_trade_fraction=0.10,
        delayed_execution_return=0.02,
        independent_source_replicated=replicated,
        multiple_testing=MultipleTestingEvidence(
            attempted_configurations=attempted_configurations,
            probability_of_backtest_overfitting=pbo,
            sharpe_trial_std=0.10,
        ),
    )


def test_tournament_rejects_unreplicated_candidate_even_when_profitable():
    evidence = _profitable_candidate("candidate", replicated=False)
    decision = evaluate_candidate(evidence)
    assert not decision.passed
    assert "independent_source_replication_missing" in decision.failures


def test_tournament_rejects_missing_multiple_testing_evidence():
    candidate = _profitable_candidate("candidate")
    without_selection_evidence = CandidateEvidence(
        candidate_id=candidate.candidate_id,
        returns=candidate.returns,
        stressed_returns=candidate.stressed_returns,
        decisions=candidate.decisions,
        sequential_window_returns=candidate.sequential_window_returns,
        stressed_window_returns=candidate.stressed_window_returns,
        asset_contributions=candidate.asset_contributions,
        largest_trade_fraction=candidate.largest_trade_fraction,
        delayed_execution_return=candidate.delayed_execution_return,
        independent_source_replicated=True,
    )
    decision = evaluate_candidate(without_selection_evidence)
    assert not decision.passed
    assert "multiple_testing_evidence_missing" in decision.failures


def test_tournament_enforces_pbo_and_deflated_sharpe():
    high_pbo = evaluate_candidate(_profitable_candidate("high-pbo", pbo=0.40))
    assert "probability_of_backtest_overfitting_above_0_20" in high_pbo.failures

    too_many_trials = evaluate_candidate(
        _profitable_candidate("many-trials", attempted_configurations=1_000_000)
    )
    assert "deflated_sharpe_probability_below_0_95" in too_many_trials.failures


def test_valid_candidate_passes_and_outranks_rejected_high_return_candidate():
    valid = _profitable_candidate("valid")
    rejected = _profitable_candidate("rejected", pbo=0.60)
    valid_decision = evaluate_candidate(valid)
    assert valid_decision.passed
    ranked = rank_candidates((rejected, valid))
    assert ranked[0][0] == "valid"


def test_sealed_prediction_is_deterministic_and_paper_only():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prediction = SealedPrediction(
        candidate_id="v5",
        decision_time=start,
        horizon_end=start + timedelta(days=3),
        weights={"BTC": 0.05, "ETH": 0.05},
        cash_weight=0.90,
        model_fingerprint="m",
        data_fingerprint="d",
    )
    assert verify_seal(prediction, prediction.seal)
    with pytest.raises(ValueError, match="paper-only"):
        SealedPrediction(
            candidate_id="bad",
            decision_time=start,
            horizon_end=start + timedelta(days=1),
            weights={},
            cash_weight=1.0,
            model_fingerprint="m",
            data_fingerprint="d",
            authorizes_trading=True,
        )

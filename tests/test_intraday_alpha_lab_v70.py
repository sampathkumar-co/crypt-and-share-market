from __future__ import annotations

import pytest

from tradebot.research import intraday_alpha_lab_v70 as v70


def passing_evidence() -> v70.CandidateEvidence:
    return v70.CandidateEvidence(
        candidate_id="trend-a",
        family=v70.CandidateFamily.HOURLY_TREND,
        standard_fold_excess=(0.01,) * 8,
        stress_fold_excess=(0.005,) * 8,
        standard_compounded_excess=0.10,
        stress_compounded_excess=0.05,
        first_half_excess=0.02,
        second_half_excess=0.03,
        delayed_stress_excess=0.01,
        best_trade_removed_stress_excess=0.02,
        best_month_removed_stress_excess=0.01,
        maximum_drawdown=0.03,
        maximum_positive_trade_share=0.10,
        maximum_positive_month_share=0.20,
        target_changing_actions=80,
        dsr_probability=0.97,
        pbo=0.10,
        minimum_track_record_satisfied=True,
        independent_source_replication_passed=True,
        trial_count=1,
    )


def test_passing_candidate_remains_paper_only() -> None:
    decision = v70.evaluate_candidate(passing_evidence())
    assert decision.passed is True
    assert decision.reasons == ()
    assert decision.paper_only is True
    assert decision.authorizes_trading is False


def test_high_return_cannot_bypass_missing_statistical_evidence() -> None:
    evidence = passing_evidence()
    rejected = v70.CandidateEvidence(
        **{
            **evidence.__dict__,
            "standard_compounded_excess": 10.0,
            "stress_compounded_excess": 8.0,
            "dsr_probability": 0.20,
            "pbo": 0.80,
        }
    )
    decision = v70.evaluate_candidate(rejected)
    assert decision.passed is False
    assert "deflated_sharpe_failed" in decision.reasons
    assert "pbo_failed" in decision.reasons


def test_trade_requires_stress_cost_and_profit_buffer() -> None:
    assert v70.lower_bound_trade_is_eligible(50.0) is False
    assert v70.lower_bound_trade_is_eligible(50.0001) is True


def test_target_rejects_short_and_excess_exposure() -> None:
    with pytest.raises(ValueError, match="short"):
        v70.validate_target({"BTC": -0.01})
    with pytest.raises(ValueError, match="single-asset"):
        v70.validate_target({"BTC": 0.051})
    with pytest.raises(ValueError, match="aggregate"):
        v70.validate_target({"BTC": 0.05, "ETH": 0.0501})


def test_fingerprint_is_order_stable() -> None:
    evidence = passing_evidence()
    assert v70.evidence_fingerprint(evidence) == v70.evidence_fingerprint(evidence)

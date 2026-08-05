from __future__ import annotations

import pytest

from tradebot.research import intraday_alpha_lab_v70 as lab
from tradebot.research import intraday_tournament_v70 as tournament


def folds() -> tuple[tournament.FoldResult, ...]:
    return tuple(
        tournament.FoldResult(
            fold_id=f"fold-{index}",
            family=lab.CandidateFamily.HOURLY_TREND,
            standard_excess=0.01,
            stress_excess=0.005,
            delayed_stress_excess=0.002,
            action_count=8,
            embargo_hours=24,
        )
        for index in range(8)
    )


def compound(value: float, count: int) -> float:
    return (1.0 + value) ** count - 1.0


def diagnostics() -> tournament.TournamentDiagnostics:
    return tournament.TournamentDiagnostics(
        standard_compounded_excess=compound(0.01, 8),
        stress_compounded_excess=compound(0.005, 8),
        first_half_excess=compound(0.01, 4),
        second_half_excess=compound(0.01, 4),
        best_trade_removed_stress_excess=0.01,
        best_month_removed_stress_excess=0.01,
        maximum_drawdown=0.02,
        maximum_positive_trade_share=0.10,
        maximum_positive_month_share=0.20,
        dsr_probability=0.97,
        pbo=0.10,
        minimum_track_record_satisfied=True,
        independent_source_replication_passed=True,
    )


def test_builds_evidence_without_touching_holdout() -> None:
    evidence = tournament.build_evidence(
        "trend-a",
        lab.CandidateFamily.HOURLY_TREND,
        folds(),
        diagnostics(),
        trial_count=4,
    )
    assert evidence.target_changing_actions == 64
    assert evidence.trial_count == 4
    assert evidence.delayed_stress_excess == pytest.approx(compound(0.002, 8))
    assert lab.evaluate_candidate(evidence).passed is True


def test_rejects_holdout_contact_and_short_embargo() -> None:
    touched = list(folds())
    touched[0] = tournament.FoldResult(**{**touched[0].__dict__, "holdout_touched": True})
    with pytest.raises(ValueError, match="holdout"):
        tournament.validate_folds(touched)

    unpurged = list(folds())
    unpurged[0] = tournament.FoldResult(**{**unpurged[0].__dict__, "embargo_hours": 23})
    with pytest.raises(ValueError, match="embargo"):
        tournament.validate_folds(unpurged)


def test_rejects_reported_compounding_mismatch() -> None:
    bad = tournament.TournamentDiagnostics(
        **{**diagnostics().__dict__, "stress_compounded_excess": 9.0}
    )
    with pytest.raises(ValueError, match="stress compounded"):
        tournament.build_evidence(
            "trend-a",
            lab.CandidateFamily.HOURLY_TREND,
            folds(),
            bad,
            trial_count=4,
        )


def test_rejects_family_switch_and_trial_reset() -> None:
    with pytest.raises(ValueError, match="trial_count"):
        tournament.build_evidence(
            "trend-a",
            lab.CandidateFamily.HOURLY_TREND,
            folds(),
            diagnostics(),
            trial_count=0,
        )
    with pytest.raises(ValueError, match="family mismatch"):
        tournament.build_evidence(
            "reversal-a",
            lab.CandidateFamily.SHOCK_REVERSAL,
            folds(),
            diagnostics(),
            trial_count=5,
        )


def test_ranking_prioritizes_stress_excess_before_raw_return() -> None:
    first = tournament.build_evidence(
        "strong-stress",
        lab.CandidateFamily.HOURLY_TREND,
        folds(),
        diagnostics(),
        trial_count=4,
    )
    weaker = lab.CandidateEvidence(
        **{
            **first.__dict__,
            "candidate_id": "high-standard-only",
            "standard_compounded_excess": 2.0,
            "stress_compounded_excess": 0.001,
        }
    )
    ranked = tournament.rank_survivors((weaker, first))
    assert ranked[0].candidate_id == "strong-stress"

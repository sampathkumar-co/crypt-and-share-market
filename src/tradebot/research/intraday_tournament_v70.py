from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from tradebot.research.intraday_alpha_lab_v70 import (
    CandidateEvidence,
    CandidateFamily,
    MIN_WALK_FORWARD_FOLDS,
)


@dataclass(frozen=True)
class FoldResult:
    fold_id: str
    family: CandidateFamily
    standard_excess: float
    stress_excess: float
    delayed_stress_excess: float
    action_count: int
    embargo_hours: int
    holdout_touched: bool = False


@dataclass(frozen=True)
class TournamentDiagnostics:
    standard_compounded_excess: float
    stress_compounded_excess: float
    first_half_excess: float
    second_half_excess: float
    best_trade_removed_stress_excess: float
    best_month_removed_stress_excess: float
    maximum_drawdown: float
    maximum_positive_trade_share: float
    maximum_positive_month_share: float
    dsr_probability: float
    pbo: float
    minimum_track_record_satisfied: bool
    independent_source_replication_passed: bool


def _compound(values: Iterable[float]) -> float:
    wealth = 1.0
    for value in values:
        if float(value) <= -1.0:
            raise ValueError("fold return cannot be less than or equal to -100%")
        wealth *= 1.0 + float(value)
    return wealth - 1.0


def validate_folds(folds: Sequence[FoldResult]) -> None:
    if len(folds) < MIN_WALK_FORWARD_FOLDS:
        raise ValueError("at least eight purged walk-forward folds are required")
    ids = [fold.fold_id for fold in folds]
    if len(ids) != len(set(ids)):
        raise ValueError("fold identifiers must be unique")
    if any(fold.embargo_hours < 24 for fold in folds):
        raise ValueError("one-day embargo is required")
    if any(fold.holdout_touched for fold in folds):
        raise ValueError("sealed holdout was touched")
    if any(fold.action_count < 0 for fold in folds):
        raise ValueError("action counts cannot be negative")


def build_evidence(
    candidate_id: str,
    family: CandidateFamily,
    folds: Sequence[FoldResult],
    diagnostics: TournamentDiagnostics,
    trial_count: int,
) -> CandidateEvidence:
    validate_folds(folds)
    if not candidate_id.strip():
        raise ValueError("candidate_id is required")
    if trial_count < 1:
        raise ValueError("trial_count must be permanent and positive")
    if any(fold.family is not family for fold in folds):
        raise ValueError("fold family mismatch")

    standard = tuple(float(fold.standard_excess) for fold in folds)
    stress = tuple(float(fold.stress_excess) for fold in folds)
    delayed = tuple(float(fold.delayed_stress_excess) for fold in folds)
    midpoint = len(folds) // 2

    calculated_standard = _compound(standard)
    calculated_stress = _compound(stress)
    if abs(calculated_standard - diagnostics.standard_compounded_excess) > 1e-12:
        raise ValueError("standard compounded excess mismatch")
    if abs(calculated_stress - diagnostics.stress_compounded_excess) > 1e-12:
        raise ValueError("stress compounded excess mismatch")
    if abs(_compound(standard[:midpoint]) - diagnostics.first_half_excess) > 1e-12:
        raise ValueError("first-half excess mismatch")
    if abs(_compound(standard[midpoint:]) - diagnostics.second_half_excess) > 1e-12:
        raise ValueError("second-half excess mismatch")

    return CandidateEvidence(
        candidate_id=candidate_id,
        family=family,
        standard_fold_excess=standard,
        stress_fold_excess=stress,
        standard_compounded_excess=calculated_standard,
        stress_compounded_excess=calculated_stress,
        first_half_excess=diagnostics.first_half_excess,
        second_half_excess=diagnostics.second_half_excess,
        delayed_stress_excess=_compound(delayed),
        best_trade_removed_stress_excess=diagnostics.best_trade_removed_stress_excess,
        best_month_removed_stress_excess=diagnostics.best_month_removed_stress_excess,
        maximum_drawdown=diagnostics.maximum_drawdown,
        maximum_positive_trade_share=diagnostics.maximum_positive_trade_share,
        maximum_positive_month_share=diagnostics.maximum_positive_month_share,
        target_changing_actions=sum(fold.action_count for fold in folds),
        dsr_probability=diagnostics.dsr_probability,
        pbo=diagnostics.pbo,
        minimum_track_record_satisfied=diagnostics.minimum_track_record_satisfied,
        independent_source_replication_passed=diagnostics.independent_source_replication_passed,
        trial_count=trial_count,
    )


def rank_survivors(evidence: Sequence[CandidateEvidence]) -> tuple[CandidateEvidence, ...]:
    return tuple(
        sorted(
            evidence,
            key=lambda item: (
                -item.stress_compounded_excess,
                item.maximum_drawdown,
                item.maximum_positive_trade_share,
                item.maximum_positive_month_share,
                -item.standard_compounded_excess,
                item.candidate_id,
            ),
        )
    )

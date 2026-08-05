from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable, Mapping


SCHEMA_VERSION = "7.0-intraday-alpha-lab"
STANDARD_ROUND_TRIP_BPS = 20.0
STRESS_ROUND_TRIP_BPS = 40.0
PROFIT_BUFFER_BPS = 10.0
MAX_TOTAL_EXPOSURE = 0.10
MAX_ASSET_EXPOSURE = 0.05
MIN_WALK_FORWARD_FOLDS = 8
MIN_STANDARD_POSITIVE_FOLDS = 7
MIN_STRESS_POSITIVE_FOLDS = 6
MIN_ACTIONS = 60
MAX_DRAWDOWN = 0.05
MAX_TRADE_CONCENTRATION = 0.15
MAX_MONTH_CONCENTRATION = 0.30
MIN_DSR_PROBABILITY = 0.95
MAX_PBO = 0.20
ASSETS = ("BTC", "ETH")


class CandidateFamily(str, Enum):
    HOURLY_TREND = "cost_filtered_hourly_trend"
    SHOCK_REVERSAL = "post_shock_reversal"
    VOLATILITY_BREAKOUT = "volatility_breakout"
    RELATIVE_STRENGTH = "btc_eth_relative_strength"


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    family: CandidateFamily
    standard_fold_excess: tuple[float, ...]
    stress_fold_excess: tuple[float, ...]
    standard_compounded_excess: float
    stress_compounded_excess: float
    first_half_excess: float
    second_half_excess: float
    delayed_stress_excess: float
    best_trade_removed_stress_excess: float
    best_month_removed_stress_excess: float
    maximum_drawdown: float
    maximum_positive_trade_share: float
    maximum_positive_month_share: float
    target_changing_actions: int
    dsr_probability: float
    pbo: float
    minimum_track_record_satisfied: bool
    independent_source_replication_passed: bool
    trial_count: int


@dataclass(frozen=True)
class PromotionDecision:
    candidate_id: str
    passed: bool
    reasons: tuple[str, ...]
    paper_only: bool = True
    authorizes_trading: bool = False


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def evidence_fingerprint(evidence: CandidateEvidence) -> str:
    return hashlib.sha256(canonical_json(asdict(evidence)).encode("utf-8")).hexdigest()


def _positive_count(values: Iterable[float]) -> int:
    return sum(float(value) > 0.0 for value in values)


def validate_target(target: Mapping[str, float]) -> None:
    unknown = set(target) - set(ASSETS)
    if unknown:
        raise ValueError(f"unsupported assets: {sorted(unknown)}")
    if any(float(weight) < 0.0 for weight in target.values()):
        raise ValueError("short exposure is forbidden")
    if sum(float(weight) for weight in target.values()) > MAX_TOTAL_EXPOSURE + 1e-12:
        raise ValueError("aggregate exposure cap exceeded")
    if any(float(weight) > MAX_ASSET_EXPOSURE + 1e-12 for weight in target.values()):
        raise ValueError("single-asset exposure cap exceeded")


def lower_bound_trade_is_eligible(lower_bound_edge_bps: float) -> bool:
    return float(lower_bound_edge_bps) > STRESS_ROUND_TRIP_BPS + PROFIT_BUFFER_BPS


def evaluate_candidate(evidence: CandidateEvidence) -> PromotionDecision:
    reasons: list[str] = []
    if len(evidence.standard_fold_excess) < MIN_WALK_FORWARD_FOLDS:
        reasons.append("insufficient_walk_forward_folds")
    if len(evidence.stress_fold_excess) != len(evidence.standard_fold_excess):
        reasons.append("fold_alignment_failed")
    if _positive_count(evidence.standard_fold_excess) < MIN_STANDARD_POSITIVE_FOLDS:
        reasons.append("standard_fold_breadth_failed")
    if _positive_count(evidence.stress_fold_excess) < MIN_STRESS_POSITIVE_FOLDS:
        reasons.append("stress_fold_breadth_failed")
    if evidence.standard_compounded_excess <= 0.0:
        reasons.append("standard_excess_not_positive")
    if evidence.stress_compounded_excess <= 0.0:
        reasons.append("stress_excess_not_positive")
    if evidence.first_half_excess <= 0.0 or evidence.second_half_excess <= 0.0:
        reasons.append("chronological_half_stability_failed")
    if evidence.delayed_stress_excess <= 0.0:
        reasons.append("delayed_execution_failed")
    if evidence.best_trade_removed_stress_excess <= 0.0:
        reasons.append("best_trade_removal_failed")
    if evidence.best_month_removed_stress_excess <= 0.0:
        reasons.append("best_month_removal_failed")
    if evidence.maximum_drawdown > MAX_DRAWDOWN:
        reasons.append("drawdown_gate_failed")
    if evidence.maximum_positive_trade_share > MAX_TRADE_CONCENTRATION:
        reasons.append("trade_concentration_failed")
    if evidence.maximum_positive_month_share > MAX_MONTH_CONCENTRATION:
        reasons.append("month_concentration_failed")
    if evidence.target_changing_actions < MIN_ACTIONS:
        reasons.append("insufficient_actions")
    if evidence.dsr_probability < MIN_DSR_PROBABILITY:
        reasons.append("deflated_sharpe_failed")
    if evidence.pbo > MAX_PBO:
        reasons.append("pbo_failed")
    if not evidence.minimum_track_record_satisfied:
        reasons.append("minimum_track_record_failed")
    if not evidence.independent_source_replication_passed:
        reasons.append("independent_replication_failed")
    if evidence.trial_count < 1:
        reasons.append("invalid_trial_count")
    return PromotionDecision(
        candidate_id=evidence.candidate_id,
        passed=not reasons,
        reasons=tuple(reasons),
    )

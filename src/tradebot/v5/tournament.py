from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import sqrt
from statistics import mean
from typing import Mapping, Sequence

from .metrics import PerformanceMetrics, contribution_concentration, performance_metrics


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    returns: tuple[float, ...]
    stressed_returns: tuple[float, ...]
    decisions: int
    sequential_window_returns: tuple[float, ...]
    stressed_window_returns: tuple[float, ...]
    asset_contributions: Mapping[str, float]
    largest_trade_fraction: float
    delayed_execution_return: float
    independent_source_replicated: bool

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if len(self.returns) != len(self.stressed_returns):
            raise ValueError("standard and stressed series must align")
        if not 0 <= self.largest_trade_fraction <= 1:
            raise ValueError("largest_trade_fraction must be in [0, 1]")

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=list)
        return sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    passed: bool
    failures: tuple[str, ...]
    standard: PerformanceMetrics
    stressed: PerformanceMetrics
    evidence_fingerprint: str


def _bootstrap_lower_mean(values: Sequence[float]) -> float:
    """Conservative deterministic normal lower bound used as a dependency-free screen."""
    if len(values) < 2:
        return float("-inf")
    center = mean(values)
    variance = sum((value - center) ** 2 for value in values) / (len(values) - 1)
    return center - 1.96 * sqrt(variance / len(values))


def evaluate_candidate(evidence: CandidateEvidence, *, periods_per_year: int = 365) -> PromotionDecision:
    standard = performance_metrics(evidence.returns, periods_per_year)
    stressed = performance_metrics(evidence.stressed_returns, periods_per_year)
    failures: list[str] = []
    if standard.annualized_return < 0.05:
        failures.append("annualized_return_below_5_percent")
    if stressed.compounded_return <= 0:
        failures.append("nonpositive_stress_return")
    if standard.maximum_drawdown > 0.10:
        failures.append("drawdown_above_10_percent")
    if sum(value > 0 for value in evidence.sequential_window_returns) < 4:
        failures.append("fewer_than_4_of_5_positive_windows")
    if sum(value > 0 for value in evidence.stressed_window_returns) < 4:
        failures.append("fewer_than_4_of_5_positive_stress_windows")
    if evidence.decisions < 30:
        failures.append("fewer_than_30_decisions")
    if len([value for value in evidence.asset_contributions.values() if value > 0]) < 3:
        failures.append("insufficient_asset_breadth")
    if contribution_concentration(evidence.asset_contributions) > 0.70:
        failures.append("asset_contribution_too_concentrated")
    if evidence.largest_trade_fraction > 0.20:
        failures.append("single_trade_contribution_above_20_percent")
    if evidence.delayed_execution_return <= 0:
        failures.append("fails_delayed_execution")
    if _bootstrap_lower_mean(evidence.stressed_returns) <= 0:
        failures.append("stress_mean_not_statistically_positive")
    if not evidence.independent_source_replicated:
        failures.append("independent_source_replication_missing")
    return PromotionDecision(
        status="HISTORICAL_CANDIDATE" if not failures else "REJECTED",
        passed=not failures,
        failures=tuple(failures),
        standard=standard,
        stressed=stressed,
        evidence_fingerprint=evidence.fingerprint,
    )


def rank_candidates(candidates: Sequence[CandidateEvidence]) -> list[tuple[str, float, PromotionDecision]]:
    ranked = []
    for candidate in candidates:
        decision = evaluate_candidate(candidate)
        score = (
            decision.stressed.compounded_return
            - 2.0 * decision.standard.maximum_drawdown
            + 0.1 * decision.standard.sharpe
        )
        if not decision.passed:
            score -= 10.0
        ranked.append((candidate.candidate_id, score, decision))
    return sorted(ranked, key=lambda item: item[1], reverse=True)

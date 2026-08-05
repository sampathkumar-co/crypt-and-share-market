from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import sqrt
from statistics import mean
from typing import Mapping, Sequence

from .metrics import PerformanceMetrics, contribution_concentration, performance_metrics
from .multiple_testing import deflated_sharpe_probability, minimum_track_record_length


@dataclass(frozen=True)
class MultipleTestingEvidence:
    """Frozen selection-bias evidence produced before sealed evaluation."""

    attempted_configurations: int
    probability_of_backtest_overfitting: float
    sharpe_trial_std: float
    return_skewness: float = 0.0
    return_excess_kurtosis: float = 0.0

    def __post_init__(self) -> None:
        if self.attempted_configurations < 1:
            raise ValueError("attempted_configurations must count every attempted configuration")
        if not 0.0 <= self.probability_of_backtest_overfitting <= 1.0:
            raise ValueError("probability_of_backtest_overfitting must be in [0, 1]")
        if self.sharpe_trial_std <= 0:
            raise ValueError("sharpe_trial_std must be positive")


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
    multiple_testing: MultipleTestingEvidence | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if len(self.returns) != len(self.stressed_returns):
            raise ValueError("standard and stressed series must align")
        if len(self.sequential_window_returns) != 5:
            raise ValueError("exactly five sequential windows are required")
        if len(self.stressed_window_returns) != 5:
            raise ValueError("exactly five stressed sequential windows are required")
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
    deflated_sharpe_probability: float
    probability_of_backtest_overfitting: float
    minimum_track_record_observations: int


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
    if sum(value > 0 for value in evidence.stressed_window_returns) < 3:
        failures.append("fewer_than_3_of_5_positive_stress_windows")
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

    dsr_probability = 0.0
    pbo = 1.0
    minimum_observations = 2**31 - 1
    selection = evidence.multiple_testing
    if selection is None:
        failures.append("multiple_testing_evidence_missing")
    else:
        pbo = selection.probability_of_backtest_overfitting
        dsr_probability = deflated_sharpe_probability(
            standard.sharpe,
            number_of_trials=selection.attempted_configurations,
            observations=len(evidence.returns),
            skewness=selection.return_skewness,
            excess_kurtosis=selection.return_excess_kurtosis,
            sharpe_std=selection.sharpe_trial_std,
        )
        benchmark = 0.0
        minimum_observations = minimum_track_record_length(
            standard.sharpe,
            target_probability=0.95,
            benchmark_sharpe=benchmark,
            skewness=selection.return_skewness,
            excess_kurtosis=selection.return_excess_kurtosis,
        )
        if dsr_probability < 0.95:
            failures.append("deflated_sharpe_probability_below_0_95")
        if pbo > 0.20:
            failures.append("probability_of_backtest_overfitting_above_0_20")
        if len(evidence.returns) < minimum_observations:
            failures.append("minimum_track_record_length_not_met")

    return PromotionDecision(
        status="HISTORICAL_CANDIDATE" if not failures else "REJECTED",
        passed=not failures,
        failures=tuple(failures),
        standard=standard,
        stressed=stressed,
        evidence_fingerprint=evidence.fingerprint,
        deflated_sharpe_probability=dsr_probability,
        probability_of_backtest_overfitting=pbo,
        minimum_track_record_observations=minimum_observations,
    )


def rank_candidates(candidates: Sequence[CandidateEvidence]) -> list[tuple[str, tuple[float, ...], PromotionDecision]]:
    """Rank conservatively: passed gates, stress survival, consistency, then return.

    A rejected high-return candidate can never outrank a valid candidate. Return is
    deliberately the last major component rather than the first optimization goal.
    """
    ranked: list[tuple[str, tuple[float, ...], PromotionDecision]] = []
    for candidate in candidates:
        decision = evaluate_candidate(candidate)
        positive_stress_windows = sum(value > 0 for value in candidate.stressed_window_returns)
        ranking_key = (
            1.0 if decision.passed else 0.0,
            decision.deflated_sharpe_probability,
            -decision.probability_of_backtest_overfitting,
            float(positive_stress_windows),
            -decision.standard.maximum_drawdown,
            decision.stressed.compounded_return,
        )
        ranked.append((candidate.candidate_id, ranking_key, decision))
    return sorted(ranked, key=lambda item: item[1], reverse=True)

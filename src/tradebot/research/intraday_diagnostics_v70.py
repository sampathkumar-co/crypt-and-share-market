from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from statistics import NormalDist, fmean
from typing import Mapping, Sequence

from tradebot.research.intraday_tournament_v70 import TournamentDiagnostics


@dataclass(frozen=True)
class TradeOutcome:
    trade_id: str
    month_id: str
    stress_excess: float
    equity_return: float


@dataclass(frozen=True)
class SourceReplication:
    source: str
    stress_returns: tuple[float, ...]
    action_count: int


def _compound(values: Sequence[float]) -> float:
    wealth = 1.0
    for value in values:
        number = float(value)
        if not math.isfinite(number) or number <= -1.0:
            raise ValueError("returns must be finite and greater than -100%")
        wealth *= 1.0 + number
    return wealth - 1.0


def _moments(values: Sequence[float]) -> tuple[float, float, float, float]:
    if len(values) < 3:
        raise ValueError("at least three observations are required")
    numbers = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError("observations must be finite")
    mean = fmean(numbers)
    variance = sum((value - mean) ** 2 for value in numbers) / (len(numbers) - 1)
    if variance <= 0.0:
        return mean, 0.0, 0.0, 3.0
    stdev = math.sqrt(variance)
    skew = fmean(((value - mean) / stdev) ** 3 for value in numbers)
    kurtosis = fmean(((value - mean) / stdev) ** 4 for value in numbers)
    return mean, stdev, skew, kurtosis


def sharpe_ratio(values: Sequence[float]) -> float:
    mean, stdev, _, _ = _moments(values)
    if stdev == 0.0:
        return math.inf if mean > 0.0 else 0.0
    return mean / stdev


def deflated_sharpe_probability(values: Sequence[float], trial_count: int) -> float:
    if trial_count < 1:
        raise ValueError("trial_count must be positive and permanent")
    mean, stdev, skew, kurtosis = _moments(values)
    if stdev == 0.0:
        return 1.0 if mean > 0.0 else 0.0
    observed = mean / stdev
    if trial_count == 1:
        expected_max = 0.0
    else:
        euler_gamma = 0.5772156649015329
        normal = NormalDist()
        upper = normal.inv_cdf(1.0 - 1.0 / trial_count)
        lower = normal.inv_cdf(1.0 - 1.0 / (trial_count * math.e))
        expected_max = (1.0 - euler_gamma) * upper + euler_gamma * lower
    denominator_term = 1.0 - skew * observed + ((kurtosis - 1.0) / 4.0) * observed**2
    if denominator_term <= 0.0:
        return 0.0
    statistic = (observed - expected_max) * math.sqrt(len(values) - 1) / math.sqrt(denominator_term)
    return min(1.0, max(0.0, NormalDist().cdf(statistic)))


def minimum_track_record_length(
    values: Sequence[float], confidence: float = 0.95, reference_sharpe: float = 0.0
) -> int:
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be between 0.5 and 1")
    mean, stdev, skew, kurtosis = _moments(values)
    if stdev == 0.0 or mean / stdev <= reference_sharpe:
        return math.inf
    observed = mean / stdev
    adjustment = 1.0 - skew * observed + ((kurtosis - 1.0) / 4.0) * observed**2
    adjustment = max(adjustment, 1e-12)
    z_score = NormalDist().inv_cdf(confidence)
    return max(3, math.ceil(1.0 + adjustment * (z_score / (observed - reference_sharpe)) ** 2))


def probability_of_backtest_overfitting(candidate_fold_returns: Sequence[Sequence[float]]) -> float:
    matrix = tuple(tuple(float(value) for value in row) for row in candidate_fold_returns)
    if len(matrix) < 2:
        raise ValueError("at least two candidates are required for PBO")
    fold_count = len(matrix[0])
    if fold_count < 8 or fold_count % 2:
        raise ValueError("PBO requires an even number of at least eight folds")
    if any(len(row) != fold_count for row in matrix):
        raise ValueError("candidate fold matrices must align")
    if not all(math.isfinite(value) for row in matrix for value in row):
        raise ValueError("candidate fold returns must be finite")

    half = fold_count // 2
    failures = 0
    partitions = 0
    all_indexes = tuple(range(fold_count))
    # Canonical half splits avoid counting each partition twice.
    for train_indexes in itertools.combinations(all_indexes, half):
        if 0 not in train_indexes:
            continue
        train_set = set(train_indexes)
        test_indexes = tuple(index for index in all_indexes if index not in train_set)
        in_sample = [fmean(row[index] for index in train_indexes) for row in matrix]
        selected = max(range(len(matrix)), key=lambda index: (in_sample[index], -index))
        out_sample = [fmean(row[index] for index in test_indexes) for row in matrix]
        selected_score = out_sample[selected]
        percentile = sum(score <= selected_score for score in out_sample) / len(out_sample)
        failures += percentile <= 0.5
        partitions += 1
    if partitions == 0:
        raise ValueError("no valid PBO partitions")
    return failures / partitions


def maximum_drawdown(equity_returns: Sequence[float]) -> float:
    wealth = 1.0
    peak = 1.0
    worst = 0.0
    for value in equity_returns:
        number = float(value)
        if not math.isfinite(number) or number <= -1.0:
            raise ValueError("equity returns must be finite and greater than -100%")
        wealth *= 1.0 + number
        peak = max(peak, wealth)
        worst = max(worst, (peak - wealth) / peak)
    return worst


def removal_and_concentration(trades: Sequence[TradeOutcome]) -> tuple[float, float, float, float]:
    if not trades:
        raise ValueError("at least one trade outcome is required")
    ids = [trade.trade_id for trade in trades]
    if len(ids) != len(set(ids)):
        raise ValueError("trade identifiers must be unique")
    stress = tuple(float(trade.stress_excess) for trade in trades)
    positive_total = sum(max(value, 0.0) for value in stress)
    best_index = max(range(len(stress)), key=lambda index: stress[index])
    best_trade_removed = _compound(stress[:best_index] + stress[best_index + 1 :])

    month_returns: dict[str, list[float]] = {}
    month_positive: dict[str, float] = {}
    for trade in trades:
        if not trade.month_id:
            raise ValueError("month_id is required")
        month_returns.setdefault(trade.month_id, []).append(float(trade.stress_excess))
        month_positive[trade.month_id] = month_positive.get(trade.month_id, 0.0) + max(
            float(trade.stress_excess), 0.0
        )
    compounded_months = {month: _compound(values) for month, values in month_returns.items()}
    best_month = max(compounded_months, key=compounded_months.get)
    best_month_removed = _compound(
        tuple(value for month, values in month_returns.items() if month != best_month for value in values)
    )
    trade_share = 0.0 if positive_total == 0.0 else max(max(value, 0.0) for value in stress) / positive_total
    month_share = (
        0.0 if positive_total == 0.0 else max(month_positive.values()) / positive_total
    )
    return best_trade_removed, best_month_removed, trade_share, month_share


def independent_source_replication_passed(replications: Sequence[SourceReplication]) -> bool:
    by_source = {item.source.lower(): item for item in replications}
    if set(by_source) != {"binance", "coinbase"}:
        return False
    binance = by_source["binance"]
    coinbase = by_source["coinbase"]
    if binance.action_count != coinbase.action_count or binance.action_count <= 0:
        return False
    if len(binance.stress_returns) != len(coinbase.stress_returns):
        return False
    if not binance.stress_returns:
        return False
    return _compound(binance.stress_returns) > 0.0 and _compound(coinbase.stress_returns) > 0.0


def build_tournament_diagnostics(
    standard_fold_returns: Sequence[float],
    stress_fold_returns: Sequence[float],
    delayed_stress_fold_returns: Sequence[float],
    trades: Sequence[TradeOutcome],
    candidate_fold_matrix: Sequence[Sequence[float]],
    replications: Sequence[SourceReplication],
    trial_count: int,
) -> TournamentDiagnostics:
    if len(standard_fold_returns) != len(stress_fold_returns):
        raise ValueError("standard and stress folds must align")
    if len(delayed_stress_fold_returns) != len(stress_fold_returns):
        raise ValueError("delayed and stress folds must align")
    midpoint = len(standard_fold_returns) // 2
    best_trade_removed, best_month_removed, trade_share, month_share = removal_and_concentration(trades)
    equity_returns = tuple(float(trade.equity_return) for trade in trades)
    required_length = minimum_track_record_length(equity_returns)
    return TournamentDiagnostics(
        standard_compounded_excess=_compound(tuple(standard_fold_returns)),
        stress_compounded_excess=_compound(tuple(stress_fold_returns)),
        first_half_excess=_compound(tuple(standard_fold_returns[:midpoint])),
        second_half_excess=_compound(tuple(standard_fold_returns[midpoint:])),
        best_trade_removed_stress_excess=best_trade_removed,
        best_month_removed_stress_excess=best_month_removed,
        maximum_drawdown=maximum_drawdown(equity_returns),
        maximum_positive_trade_share=trade_share,
        maximum_positive_month_share=month_share,
        dsr_probability=deflated_sharpe_probability(equity_returns, trial_count),
        pbo=probability_of_backtest_overfitting(candidate_fold_matrix),
        minimum_track_record_satisfied=required_length != math.inf and len(equity_returns) >= required_length,
        independent_source_replication_passed=independent_source_replication_passed(replications),
    )

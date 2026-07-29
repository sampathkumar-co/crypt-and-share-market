from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev


@dataclass(frozen=True)
class TemporalStabilityPolicy:
    """Fail-closed rules for selecting parameters across training subperiods."""

    fold_count: int = 3
    min_fold_bars: int = 30
    min_required_folds: int = 2
    min_required_active_folds: int = 2
    min_full_training_return: float = 0.0
    min_positive_fold_fraction: float = 0.67
    min_positive_active_fold_fraction: float = 0.50
    min_mean_active_fold_return: float = 0.0
    min_worst_fold_return: float = -0.03
    max_return_dispersion: float = 0.08
    min_total_fold_trades: int = 2
    uncertainty_penalty: float = 0.50
    worst_fold_weight: float = 0.25

    def __post_init__(self) -> None:
        if self.fold_count < 1:
            raise ValueError("fold_count must be positive")
        if self.min_fold_bars < 10:
            raise ValueError("min_fold_bars must be at least 10")
        if not 1 <= self.min_required_folds <= self.fold_count:
            raise ValueError("min_required_folds must be within fold_count")
        if not 1 <= self.min_required_active_folds <= self.fold_count:
            raise ValueError("min_required_active_folds must be within fold_count")
        if not 0 <= self.min_positive_fold_fraction <= 1:
            raise ValueError("min_positive_fold_fraction must be between 0 and 1")
        if not 0 <= self.min_positive_active_fold_fraction <= 1:
            raise ValueError("min_positive_active_fold_fraction must be between 0 and 1")
        if self.max_return_dispersion < 0:
            raise ValueError("max_return_dispersion cannot be negative")
        if self.min_total_fold_trades < 0:
            raise ValueError("min_total_fold_trades cannot be negative")
        if self.uncertainty_penalty < 0 or self.worst_fold_weight < 0:
            raise ValueError("stability score weights cannot be negative")


def temporal_fold_ranges(length: int, policy: TemporalStabilityPolicy) -> list[tuple[int, int]]:
    """Return contiguous, non-overlapping training fold ranges."""
    if length <= 0:
        return []
    available = max(1, length // policy.min_fold_bars)
    fold_count = min(policy.fold_count, available)
    base, remainder = divmod(length, fold_count)
    ranges: list[tuple[int, int]] = []
    start = 0
    for index in range(fold_count):
        size = base + (1 if index < remainder else 0)
        end = start + size
        ranges.append((start, end))
        start = end
    return ranges


def summarize_fold_metrics(
    fold_metrics: list[dict[str, float | int]],
) -> dict[str, float | int | bool]:
    returns = [float(item.get("net_return", 0.0)) for item in fold_metrics]
    trades = [int(item.get("trades", 0)) for item in fold_metrics]
    drawdowns = [float(item.get("max_drawdown", 0.0)) for item in fold_metrics]
    active_returns = [value for value, count in zip(returns, trades) if count > 0]
    positive_fraction = (
        sum(value > 0 for value in returns) / len(returns)
        if returns
        else 0.0
    )
    positive_active_fraction = (
        sum(value > 0 for value in active_returns) / len(active_returns)
        if active_returns
        else 0.0
    )
    return {
        "fold_count": len(returns),
        "positive_fold_fraction": positive_fraction,
        "active_fold_count": len(active_returns),
        "inactive_fold_count": len(returns) - len(active_returns),
        "positive_active_fold_fraction": positive_active_fraction,
        "mean_fold_return": mean(returns) if returns else 0.0,
        "mean_active_fold_return": mean(active_returns) if active_returns else 0.0,
        "worst_fold_return": min(returns) if returns else 0.0,
        "worst_active_fold_return": min(active_returns) if active_returns else 0.0,
        "best_fold_return": max(returns) if returns else 0.0,
        "return_dispersion": pstdev(returns) if len(returns) > 1 else 0.0,
        "active_return_dispersion": pstdev(active_returns) if len(active_returns) > 1 else 0.0,
        "total_fold_trades": sum(trades),
        "max_fold_drawdown": max(drawdowns, default=0.0),
        "stable": False,
    }


def stability_reasons(
    full_metrics: dict[str, float | int],
    summary: dict[str, float | int | bool],
    policy: TemporalStabilityPolicy,
) -> list[str]:
    reasons: list[str] = []
    if int(summary["fold_count"]) < policy.min_required_folds:
        reasons.append("insufficient_temporal_training_folds")
    if float(full_metrics.get("net_return", 0.0)) <= policy.min_full_training_return:
        reasons.append("full_training_return_not_positive")
    if int(summary["active_fold_count"]) < policy.min_required_active_folds:
        reasons.append("insufficient_active_training_folds")
    if float(summary["mean_active_fold_return"]) <= policy.min_mean_active_fold_return:
        reasons.append("mean_active_training_return_not_positive")
    if float(summary["positive_active_fold_fraction"]) < policy.min_positive_active_fold_fraction:
        reasons.append("too_few_positive_active_training_folds")
    if float(summary["worst_active_fold_return"]) < policy.min_worst_fold_return:
        reasons.append("worst_active_training_fold_too_negative")
    if float(summary["active_return_dispersion"]) > policy.max_return_dispersion:
        reasons.append("active_training_returns_too_unstable")
    if int(summary["total_fold_trades"]) < policy.min_total_fold_trades:
        reasons.append("too_few_training_trades")
    return reasons


def stability_adjusted_score(
    base_score: float,
    summary: dict[str, float | int | bool],
    policy: TemporalStabilityPolicy,
) -> float:
    return (
        base_score
        + float(summary["mean_active_fold_return"]) * 0.35
        + float(summary["worst_active_fold_return"]) * policy.worst_fold_weight
        + float(summary["positive_active_fold_fraction"]) * 0.04
        - float(summary["active_return_dispersion"]) * policy.uncertainty_penalty
        - float(summary["max_fold_drawdown"]) * 0.10
    )


def with_stability_flag(
    summary: dict[str, float | int | bool],
    reasons: list[str],
) -> dict[str, float | int | bool]:
    return {**summary, "stable": not reasons}
